"""Retrieve the dictionary definitions."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import logging
import time
import shutil
from collections import Counter, defaultdict

import numpy
import json
import nltk
from wordnik import swagger, WordApi, AccountApi

from dictlearn.util import vec2str

logger = logging.getLogger(__name__)

_MIN_REMAINING_CALLS = 100
_SAVE_EVERY_CALLS = 100

class Dictionary(object):
    """The dictionary of definitions.

    The native representation of the dictionary is a mapping from a word
    to a list of definitions, each of which is a sequence of words. All
    the words are stored as strings.

    """
    def __init__(self, path=None):
        self._data = {}
        self._path = path
        self._tmp_path = os.path.join(os.path.dirname(path),
                                         self._path + '.tmp')
        if self._path and os.path.exists(self._path):
            self.load(self._path)

    def load(self, path):
        with open(path, 'r') as src:
            self._data = json.load(src)

    def _wait_until_quota_reset(self):
        while True:
            status = self._account_api.getApiTokenStatus()
            logger.debug("{} remaining calls".format(status.remainingCalls))
            if status.remainingCalls >= _MIN_REMAINING_CALLS:
                logger.debug("Wordnik quota was resetted")
                self._remaining_calls = status.remainingCalls
                return
            logger.debug("sleep until quota reset")
            time.sleep(60.)


    def crawl(self, vocab, api_key, call_quota=15000):
        """Download and preprocess definitions from Wordnik.

        vocab
            Vocabulary for which the definitions should be found.
        api_key
            The API key to use in communications with Wordnik.
        call_quota
            Maximum number of calls per hour.

        """
        self._remaining_calls = call_quota
        self._last_saved = 0

        client = swagger.ApiClient(
            api_key, 'https://api.wordnik.com/v4')
        self._word_api = WordApi.WordApi(client)
        self._account_api = AccountApi.AccountApi(client)
        toktok = nltk.ToktokTokenizer()

        def save():
            logger.debug("saving...")
            with open(self._tmp_path, 'w') as dst:
                json.dump(self._data, dst, indent=2)
            shutil.move(self._tmp_path, self._path)
            logger.debug("saved")
            self._last_saved = 0

        # Here, for now, we don't do any stemming or lemmatization.
        # Stemming is useless because the dictionary is not indexed with
        # lemmas, not stems. Lemmatizers, on the other hand, can not be
        # fully trusted when it comes to unknown words.
        for word in vocab.words:
            if word in self._data:
                logger.debug("a known word {}, skip".format(word))
                continue

            if self._last_saved >= _SAVE_EVERY_CALLS:
                save()

            # 100 is a safery margin, I don't want to DDoS Wordnik :)
            if self._remaining_calls < _MIN_REMAINING_CALLS:
                self._wait_until_quota_reset()
            definitions = self._word_api.getDefinitions(word)
            self._remaining_calls -= 1
            self._last_saved += 1

            if not definitions:
                definitions = []
            self._data[word] = []
            for def_ in definitions:
                tokenized_def = [token.encode('utf-8')
                                 for token in toktok.tokenize(def_.text.lower())]
                self._data[word].append(tokenized_def)
            logger.debug("definitions for '{}' fetched".format(word))
        save()

    def get_definitions(self, key):
        return self._data.get(key, [])


class Retrieval(object):

    def __init__(self, vocab, dictionary):
        self._vocab = vocab
        self._dictionary = dictionary
        self._stemmer = nltk.PorterStemmer()

        # Preprocess all the definitions to see token ids instead of chars

    def retrieve(self, batch):
        """Retrieves all definitions for a batch of words sequences.

        TODO: definitions of phrases, phrasal verbs, etc.

        """
        definitions = []
        stem_def_indices = defaultdict(list)
        def_map = []

        for seq_pos, sequence in enumerate(batch):
            for word_pos, word in enumerate(sequence):
                if isinstance(word, numpy.ndarray):
                    word = vec2str(word)
                stem  = self._stemmer.stem(word)
                if stem not in stem_def_indices:
                    # The first time a stem is encountered in a batch
                    stem_defs = self._dictionary.get_definitions(stem)
                    if not stem_defs:
                        # No defition for this stem
                        continue
                    for i, def_ in enumerate(stem_defs):
                        def_ = [self._vocab.word_to_id(token) for token in def_]
                        stem_def_indices[stem].append(len(definitions))
                        definitions.append(def_)
                for def_index in stem_def_indices[stem]:
                    def_map.append((seq_pos, word_pos, def_index))

        return definitions, def_map
