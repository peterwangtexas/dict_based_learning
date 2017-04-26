"""
Methods of constructing word embeddings

TODO(kudkudak): Competitive multimodal might be one way. But
also it might be useful to ecnourage learning complementary features
Why would FC work worse? Maybe with shareable def lookup it would?

Idea: Learn to pick by gating that's learnable (and would ignore unknown words)
Idea: Then start tarining gate late?

"""
from blocks.bricks import Initializable, Linear, MLP, Tanh, Rectifier
from blocks.bricks.base import application, _variable_name
from blocks.bricks.lookup import LookupTable
from blocks.bricks.recurrent import LSTM
from blocks.bricks.simple import Softmax
from blocks.bricks.bn import BatchNormalization
from blocks.initialization import Uniform, Constant
from blocks.bricks import Softmax, Rectifier, Logistic

import theano
import theano.tensor as T

from dictlearn.inits import GlorotUniform
from dictlearn.util import masked_root_mean_square, apply_dropout
from dictlearn.ops import RetrievalOp

import logging
logger = logging.getLogger(__file__)


class LSTMReadDefinitions(Initializable):
    """
    Converts definition into embeddings.

    Parameters
    ----------
    num_input_words: int, default: -1
        If non zero will (a bit confusing name) restrict dynamically vocab.
        WARNING: it assumes word ids are monotonical with frequency!

    dim : int
        Dimensionality of the def rnn.

    emb_dim : int
        Dimensionality of word embeddings

    """
    def __init__(self, num_input_words, emb_dim, dim, vocab, lookup=None, **kwargs):
        self._num_input_words = num_input_words
        self._vocab = vocab

        children = []

        if lookup is None:
            # TODO: Does it make sense it is self._num_input_words?
            # Check definition coverage
            self._def_lookup = LookupTable(self._num_input_words, emb_dim, name='def_lookup')
            children.append(self._def_lookup)
        else:
            self._def_lookup = lookup
            # TODO(kudkudk): Should I add to children if I just pass it?

        self._def_fork = Linear(emb_dim, 4 * dim, name='def_fork')
        self._def_rnn = LSTM(dim, name='def_rnn')
        children.extend([self._def_fork, self._def_rnn])

        super(LSTMReadDefinitions, self).__init__(children=children, **kwargs)


    @application
    def apply(self, application_call,
              defs, def_mask):
        """
        Returns vector per each word in sequence using the dictionary based lookup
        """
        # Short listing
        defs = (T.lt(defs, self._num_input_words) * defs
                + T.ge(defs, self._num_input_words) * self._vocab.unk)

        embedded_def_words = self._def_lookup.apply(defs)
        def_embeddings = self._def_rnn.apply(
            T.transpose(self._def_fork.apply(embedded_def_words), (1, 0, 2)),
            mask=def_mask.T)[0][-1]

        return def_embeddings




class MeanPoolReadDefinitions(Initializable):
    """
    Converts definition into embeddings using simple sum + translation

    Parameters
    ----------
    num_input_words: int, default: -1
        If non zero will (a bit confusing name) restrict dynamically vocab.
        WARNING: it assumes word ids are monotonical with frequency!

    dim : int
        Dimensionality of the def rnn.

    emb_dim : int
        Dimensionality of word embeddings

    """
    def __init__(self, num_input_words, emb_dim, dim, vocab, gating="none", lookup=None, **kwargs):
        self._num_input_words = num_input_words
        self._vocab = vocab

        children = []

        if lookup is None:
            self._def_lookup = LookupTable(self._num_input_words, emb_dim, name='def_lookup')
            children.append(self._def_lookup)
        else:
            self._def_lookup = lookup
            # TODO(kudkudak): Should I add to children if I just pass it?

        if gating == "none":
            pass
        elif gating == "multiplicative":
            raise NotImplementedError()
        else:
            raise NotImplementedError()

        # TODO(kudkudak): Does this make sense, given that WVh = (WV)h ? I think encouraging
        # sparsity of gating here would work way better
        self._def_translate = Linear(emb_dim, dim, name='def_translate')
        children.append(self._def_translate)

        super(MeanPoolReadDefinitions, self).__init__(children=children, **kwargs)


    @application
    def apply(self, application_call,
              defs, def_mask):
        """
        Returns vector per each word in sequence using the dictionary based lookup
        """
        # Short listing
        defs = (T.lt(defs, self._num_input_words) * defs
                + T.ge(defs, self._num_input_words) * self._vocab.unk)
        defs_emb = self._def_lookup.apply(defs)

        # Translate. Crucial for recovering useful information from embeddings
        def_emb_flatten = defs_emb.reshape((defs_emb.shape[0] * defs_emb.shape[1], defs_emb.shape[2]))
        def_transl = self._def_translate.apply(def_emb_flatten)
        def_transl = def_transl.reshape((defs_emb.shape[0], defs_emb.shape[1], -1))

        def_emb_mask = def_mask.dimshuffle((0, 1, "x"))
        def_embeddings = (def_emb_mask * def_transl).mean(axis=1)

        return def_embeddings


class MeanPoolCombiner(Initializable):
    """
    Parameters
    ----------
    dim: int

    dropout_type: str

    dropout: float, defaut: 0.0

    emb_dim: int

    compose_type : str
        If 'sum', the definition and word embeddings are averaged
        If 'fully_connected_linear', a learned perceptron compose the 2
        embeddings linearly
        If 'fully_connected_relu', ...
        If 'fully_connected_tanh', ...
    """

    def __init__(self, emb_dim, dim, dropout=0.0,
            def_word_gating="none",
            dropout_type="per_unit", compose_type="sum",
            word_dropout_weighting="no_weighting",
            shortcut_unk_and_excluded=False,  num_input_words=-1, exclude_top_K=-1,
            **kwargs):
        self._dropout = dropout
        self._num_input_words = num_input_words
        self._exclude_top_K = exclude_top_K
        self._dropout_type = dropout_type
        self._compose_type = compose_type
        self._shortcut_unk_or_excluded = shortcut_unk_and_excluded
        self._word_dropout_weighting = word_dropout_weighting
        self._def_word_gating = def_word_gating

        if def_word_gating not in {"none", "multiplicative"}:
            raise NotImplementedError()

        if word_dropout_weighting not in {"no_weighting"}:
            raise NotImplementedError("Not implemented " + word_dropout_weighting)

        if dropout_type not in {"per_unit", "per_example", "per_word"}:
            raise NotImplementedError()

        children = []

        if self._def_word_gating== "multiplicative":
            self._gate_mlp = Linear(emb_dim, emb_dim,  weights_init=GlorotUniform(), biases_init=Constant(0))
            self._gate_act = Logistic()
            children.extend([self._gate_mlp, self._gate_act])

        if compose_type == 'fully_connected_linear':
            self._def_state_compose = MLP(activations=[None],
                dims=[emb_dim + dim, dim], weights_init=GlorotUniform(), biases_init=Constant(0))
            children.append(self._def_state_compose)
        elif compose_type == "gated_sum":

            if dropout_type == "per_word" or dropout_type == "per_example":
                raise RuntimeError("I dont think this combination makes much sense")

            self._compose_gate_mlp = Linear(2 * emb_dim, emb_dim,  weights_init=GlorotUniform(), biases_init=Constant(0))
            self._compose_gate_act = Logistic()
            children.extend([self._compose_gate_mlp, self._compose_gate_act])
        elif compose_type == 'sum':
            if not emb_dim == dim:
                raise ValueError("Embedding has different dim! Cannot use compose_type='sum'")
        else:
            raise NotImplementedError()

        super(MeanPoolCombiner, self).__init__(children=children, **kwargs)

    @application
    def apply(self, application_call,
              word_embs, words_mask,
              def_embeddings, def_map, train_phase, word_ids=False, call_name=""):
        batch_shape = word_embs.shape

        # def_map is (seq_pos, word_pos, def_index)

        # Mean-pooling of definitions
        def_sum = T.zeros((batch_shape[0] * batch_shape[1], def_embeddings.shape[1]))
        def_lens = T.zeros_like(def_sum[:, 0])
        flat_indices = def_map[:, 0] * batch_shape[1] + def_map[:, 1] # Index of word in flat

        if self._def_word_gating == "none":
            def_sum = T.inc_subtensor(def_sum[flat_indices],
                def_embeddings[def_map[:, 2]])
        elif self._def_word_gating == "multiplicative":
            gates = word_embs.reshape((batch_shape[0] * batch_shape[1], -1))
            gates = self._gate_mlp.apply(gates)
            gates = self._gate_act.apply(gates)

            application_call.add_auxiliary_variable(
                masked_root_mean_square(gates.reshape((batch_shape[0], batch_shape[1], -1)), words_mask),
                    name=call_name + '_gate_rootmean2')

            def_sum = T.inc_subtensor(def_sum[flat_indices],
                gates[flat_indices] * def_embeddings[def_map[:, 2]])
        else:
            raise NotImplementedError()

        def_lens = T.inc_subtensor(def_lens[flat_indices], 1)
        def_mean = def_sum / T.maximum(def_lens[:, None], 1)
        def_mean = def_mean.reshape((batch_shape[0], batch_shape[1], -1))

        application_call.add_auxiliary_variable(
            masked_root_mean_square(def_mean, words_mask), name=call_name + '_def_mean_rootmean2')

        if train_phase and self._dropout != 0.0:
            if self._dropout_type == "per_unit":
                logger.info("Adding per_unit drop on dict and normal emb")
                word_embs = apply_dropout(word_embs, drop_prob=self._dropout)
                def_mean = apply_dropout(def_mean, drop_prob=self._dropout)
            elif self._dropout_type == "per_example":
                logger.info("Adding per_example drop on dict and normal emb")
                # We dropout mask
                mask_defs = T.ones((batch_shape[0],))
                mask_we = T.ones((batch_shape[0],))

                # Mask dropout
                mask_defs = apply_dropout(mask_defs, drop_prob=self._dropout)
                mask_we = apply_dropout(mask_we, drop_prob=self._dropout)

                # this reduces variance. If both 0 will select both
                where_both_zero = T.eq((mask_defs + mask_we), 0)

                mask_defs = (where_both_zero + mask_defs).dimshuffle(0, "x", "x")
                mask_we = (where_both_zero + mask_we).dimshuffle(0, "x", "x")

                def_mean = mask_defs * def_mean
                word_embs = mask_we * word_embs
            elif self._dropout_type == "per_word_independent":
                # TODO: Maybe we also want to have possibility of including both (like in per_example)
                pass # TODO: implement
            elif self._dropout_type == "per_word":
                # Note dropout here just becomes preference for word embeddings.
                # The higher dropout the more likely is picking word embedding

                logger.info("Apply per_word dropou on dict and normal emb")
                mask = T.ones((batch_shape[0], batch_shape[1]))
                mask = apply_dropout(mask, drop_prob=self._dropout)
                mask = mask.dimshuffle(0, 1, "x")

                # Reduce variance: if def_mean is 0 let's call it uninformative
                # is_retrieved = T.gt(T.abs_(def_mean).sum(axis=2, keepdims=True), 0)
                # mask = mask * is_retrieved # Includes mean if: sampled AND retrieved

                # Competitive
                def_mean = mask * def_mean
                word_embs = (1 - mask) * word_embs

                # TODO: Smarter weighting (at least like divisor in dropout)

                if not self._compose_type == "sum":
                    raise NotImplementedError()

        application_call.add_auxiliary_variable(
            def_mean.copy(),
            name=call_name + '_dict_word_embeddings')

        application_call.add_auxiliary_variable(
            word_embs.copy(),
            name=call_name + '_word_embeddings')

        if self._compose_type == 'sum':
            final_embeddings = word_embs + def_mean
        elif self._compose_type == 'gated_sum':
            # How to learn here?
            concat = T.concatenate([word_embs, def_mean], axis=2)
            gates = concat.reshape((batch_shape[0] * batch_shape[1], -1))
            gates = self._compose_gate_mlp.apply(gates)
            gates = self._compose_gate_act.apply(gates)
            gates = gates.reshape((batch_shape[0], batch_shape[1], -1))
            final_embeddings = gates * word_embs + (1 - gates) * def_mean

            application_call.add_auxiliary_variable(
                masked_root_mean_square(gates.reshape((batch_shape[0], batch_shape[1], -1)), words_mask),
                name=call_name + '_compose_gate_rootmean2')
        elif self._compose_type.startswith('fully_connected'):
            concat = T.concatenate([word_embs, def_mean], axis=2)
            final_embeddings = self._def_state_compose.apply(concat)
        else:
            raise NotImplementedError()

        # Last bit is optional forcing dict or word emb in case of exclued or unks
        if self._shortcut_unk_and_excluded:
            final_embeddings = final_embeddings * T.lt(word_ids, self._num_input_words) + \
                               def_mean * T.ge(word_ids, self._num_input_words)
            final_embeddings = word_embs * T.lt(word_ids, self._exclude_top_K) + \
                               final_embeddings * T.ge(word_ids, self._exclude_top_K)

        application_call.add_auxiliary_variable(
            masked_root_mean_square(final_embeddings, words_mask),
            name=call_name + '_merged_input_rootmean2')

        return final_embeddings

