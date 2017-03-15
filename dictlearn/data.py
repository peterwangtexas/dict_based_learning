"""Dataset layout and data preparation."""

import os
import functools

from fuel.transformers import Mapping, Batch, Padding
from fuel.schemes import ConstantScheme

from dictlearn.vocab import Vocabulary
from dictlearn.text_dataset import TextDataset
from dictlearn.util import str2vec

# We have to pad all the words to contain the same
# number of characters.
MAX_NUM_CHARACTERS = 100


def vectorize(example):
    """Replaces word strings with vectors.

    example
        A a tuple of lists of word strings.

    """
    return tuple([str2vec(word, MAX_NUM_CHARACTERS) for word in source]
                 for source in example)


def add_bos(bos, example):
    return tuple([bos] + source for source in example)


class Data(object):
    """Builds the data stream for different parts of the data."""
    def __init__(self, path, layout):
        self._path = path
        self._layout = layout
        if not self._layout in ['standard']:
            raise "Only the standard layout is currently supported."

        self._vocab = None
        self._dataset_cache = {}

    @property
    def vocab(self):
        if not self._vocab:
            self._vocab = Vocabulary(os.path.join(self._path, "vocab.txt"))
        return self._vocab

    def get_dataset(self, part):
        if not part in self._dataset_cache:
            part_map = {'train': 'train.txt',
                        'valid': 'valid.txt',
                        'test': 'test.txt'}
            part_path = os.path.join(self._path, part_map[part])
            dataset = TextDataset(part_path)
            self._dataset_cache[part] = dataset
        return self._dataset_cache[part]

    def get_stream(self, part, batch_size=None):
        dataset = self.get_dataset(part)
        stream = dataset.get_example_stream()
        stream = Mapping(stream, functools.partial(add_bos, Vocabulary.BOS))
        stream = Mapping(stream, vectorize)
        if not batch_size:
            return stream
        stream = Batch(
            stream,
            iteration_scheme=ConstantScheme(batch_size))
        stream = Padding(stream)
        return stream