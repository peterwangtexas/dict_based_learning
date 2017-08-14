import numpy

import theano
from theano import tensor

from dictlearn.util import vec2str
from dictlearn.retrieval import Retrieval

class WordToIdOp(theano.Op):
    """Replaces words with their ids."""
    def __init__(self, vocab):
        self._vocab = vocab

    def make_node(self, input_):
        input_ = tensor.as_tensor_variable(input_)
        output_type = tensor.TensorType(
            input_.dtype, input_.broadcastable[:-1])
        return theano.Apply(self, [input_], [output_type()])

    def perform(self, node, inputs, output_storage):
        words = inputs[0]
        words_flat = words.reshape(-1, words.shape[-1])
        word_ids = numpy.array([self._vocab.word_to_id(vec2str(word))
                                for word in words_flat])
        output_storage[0][0] = word_ids.reshape(words.shape[:-1])

class WordToCountOp(theano.Op):
    """Replaces words with their counts."""
    def __init__(self, vocab):
        self._vocab = vocab

    def make_node(self, input_):
        input_ = tensor.as_tensor_variable(input_)
        output_type = tensor.TensorType(
            input_.dtype, input_.broadcastable[:-1])
        return theano.Apply(self, [input_], [output_type()])

    def perform(self, node, inputs, output_storage):
        words = inputs[0]
        words_flat = words.reshape(-1, words.shape[-1])
        word_counts = numpy.array([self._vocab.word_freq(vec2str(word))
                                for word in words_flat])
        output_storage[0][0] = word_counts.reshape(words.shape[:-1])


class RetrievalOp(theano.Op):
    """Retrieves the definitions from the dictionary."""
    def __init__(self, retrieval):
        self._retrieval = retrieval

    def make_node(self, input_):
        defs_type = tensor.TensorType('int64', [False, False])
        def_mask_type = tensor.TensorType('float32', [False, False])
        # both type happened to be the same, but this is just a coincidence
        def_map_type = defs_type
        return theano.Apply(
            self, [input_], [defs_type(), def_mask_type(), def_map_type()])

    def perform(self, node, inputs, output_storage):
        defs, def_mask, def_map = self._retrieval.retrieve_and_pad(inputs[0])
        output_storage[0][0] = defs
        output_storage[1][0] = def_mask
        output_storage[2][0] = def_map
