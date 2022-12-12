"""
Query and install embeddings from the HuggingFace SD Concepts Library
at https://huggingface.co/sd-concepts-library.

The interface is through the Concepts() object.
"""
import os
import re
import traceback
from typing import Callable
from urllib import request, error as ul_error
from huggingface_hub import HfFolder, hf_hub_url, ModelSearchArguments, ModelFilter, HfApi
from ldm.invoke.globals import Globals

class Concepts(object):
    def __init__(self, root=None):
        '''
        Initialize the Concepts object. May optionally pass a root directory.
        '''
        self.root = root or Globals.root
        self.hf_api = HfApi()
        self.concept_list = None
        self.concepts_loaded = dict()
        self.triggers = dict()            # concept name to trigger phrase
        self.concept_names = dict()       # trigger phrase to concept name
        self.match_trigger = re.compile('(<[\w\- >]+>)') # trigger is slightly less restrictive than HF concept name
        self.match_concept = re.compile('<([\w\-]+)>') # HF concept name can only contain A-Za-z0-9_-

    def list_concepts(self)->list:
        '''
        Return a list of all the concepts by name, without the 'sd-concepts-library' part.
        '''
        if self.concept_list is not None:
            return self.concept_list
        try:
            models = self.hf_api.list_models(filter=ModelFilter(model_name='sd-concepts-library/'))
            self.concept_list = [a.id.split('/')[1] for a in models]
        except Exception as e:
            print(f' ** WARNING: Hugging Face textual inversion concepts libraries could not be loaded. The error was {str(e)}.')
            print(' ** You may load .bin and .pt file(s) manually using the --embedding_directory argument.')
        return self.concept_list

    def get_concept_model_path(self, concept_name:str)->str:
        '''
        Returns the path to the 'learned_embeds.bin' file in
        the named concept. Returns None if invalid or cannot
        be downloaded.
        '''
        return self.get_concept_file(concept_name.lower(),'learned_embeds.bin')

    def concept_to_trigger(self, concept_name:str)->str:
        '''
        Given a concept name returns its trigger by looking in the
        "token_identifier.txt" file.
        '''
        if concept_name in self.triggers:
            return self.triggers[concept_name]
        file = self.get_concept_file(concept_name, 'token_identifier.txt', local_only=True)
        if not file:
            return None
        with open(file,'r') as f:
            trigger = f.readline()
            trigger = trigger.strip()
        self.triggers[concept_name] = trigger
        self.concept_names[trigger] = concept_name
        return trigger

    def trigger_to_concept(self, trigger:str)->str:
        '''
        Given a trigger phrase, maps it to the concept library name.
        Only works if concept_to_trigger() has previously been called
        on this library. There needs to be a persistent database for
        this.
        '''
        concept = self.concept_names.get(trigger,None)
        return f'<{concept}>' if concept else f'{trigger}'

    def replace_triggers_with_concepts(self, prompt:str)->str:
        '''
        Given a prompt string that contains <trigger> tags, replace these
        tags with the concept name. The reason for this is so that the
        concept names get stored in the prompt metadata. There is no
        controlling of colliding triggers in the SD library, so it is
        better to store the concept name (unique) than the concept trigger
        (not necessarily unique!)
        '''
        triggers = self.match_trigger.findall(prompt)
        if not triggers:
            return prompt

        def do_replace(match)->str:
            return self.trigger_to_concept(match.group(1)) or f'<{match.group(1)}>'
        return self.match_trigger.sub(do_replace, prompt)

    def replace_concepts_with_triggers(self, prompt:str, load_concepts_callback: Callable[[list], any])->str:
        '''
        Given a prompt string that contains `<concept_name>` tags, replace
        these tags with the appropriate trigger.

        If any `<concept_name>` tags are found, `load_concepts_callback()` is called with a list
        of `concepts_name` strings.
        '''
        concepts = self.match_concept.findall(prompt)
        if not concepts:
            return prompt
        load_concepts_callback(concepts)

        def do_replace(match)->str:
            return self.concept_to_trigger(match.group(1)) or f'<{match.group(1)}>'
        return self.match_concept.sub(do_replace, prompt)

    def get_concept_file(self, concept_name:str, file_name:str='learned_embeds.bin' , local_only:bool=False)->str:
        if not self.concept_is_downloaded(concept_name) and not local_only:
            self.download_concept(concept_name)
        path = os.path.join(self._concept_path(concept_name), file_name)
        return path if os.path.exists(path) else None
        
    def concept_is_downloaded(self, concept_name)->bool:
        concept_directory = self._concept_path(concept_name)
        return os.path.exists(concept_directory)
        
    def download_concept(self,concept_name)->bool:
        repo_id = self._concept_id(concept_name)
        dest = self._concept_path(concept_name)

        access_token = HfFolder.get_token()
        header = [("Authorization", f'Bearer {access_token}')] if access_token else []
        opener = request.build_opener()
        opener.addheaders = header
        request.install_opener(opener)

        os.makedirs(dest, exist_ok=True)
        succeeded = True
        
        bytes = 0
        def tally_download_size(chunk, size, total):
            nonlocal bytes
            if chunk==0:
                bytes += total

        print(f'>> Downloading {repo_id}...',end='')
        try:
            for file in ('README.md','learned_embeds.bin','token_identifier.txt','type_of_concept.txt'):
                url = hf_hub_url(repo_id, file)
                request.urlretrieve(url, os.path.join(dest,file),reporthook=tally_download_size)
        except ul_error.HTTPError as e:
            if e.code==404:
                print(f'This concept is not known to the Hugging Face library. Generation will continue without the concept.')
            else:
                print(f'Failed to download {concept_name}/{file} ({str(e)}. Generation will continue without the concept.)')
            os.rmdir(dest)
            return False
        except ul_error.URLError as e:
            print(f'ERROR: {str(e)}. This may reflect a network issue. Generation will continue without the concept.')
            os.rmdir(dest)
            return False
        print('...{:.2f}Kb'.format(bytes/1024))
        return succeeded

    def _concept_id(self, concept_name:str)->str:
        return f'sd-concepts-library/{concept_name}'

    def _concept_path(self, concept_name:str)->str:
        return os.path.join(self.root,'models','sd-concepts-library',concept_name)
