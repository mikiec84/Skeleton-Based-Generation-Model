# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
# Modifications Copyright 2017 Abigail See
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""This file contains code to build and run the tensorflow graph for the sequence-to-sequence model"""

import os
import time
import numpy as np
import tensorflow as tf
from tensorflow.contrib.tensorboard.plugins import projector

FLAGS = tf.app.flags.FLAGS


def sample_output( embedding_dec, output_projection=None,
                               given_number=None):

  def loop_function(prev,_):

    prev = tf.nn.xw_plus_b(
          prev, output_projection[0], output_projection[1])
    prev_symbol = tf.cast(tf.reshape(tf.multinomial(tf.log(prev), 1), [FLAGS.batch_size]), tf.int32)
    emb_prev = tf.nn.embedding_lookup(embedding_dec, prev_symbol)
    return emb_prev

  def loop_function_max(prev,_):
      """function that feed previous model output rather than ground truth."""
      if output_projection is not None:
          prev = tf.nn.xw_plus_b(
              prev, output_projection[0], output_projection[1])
      prev_symbol = tf.argmax(prev, 1)
      emb_prev = tf.nn.embedding_lookup(embedding_dec, prev_symbol)
      #emb_prev = tf.stop_gradient(emb_prev)
      return emb_prev



  return loop_function,loop_function_max

class Sc_Generator(object):


  def __init__(self, hps, vocab):
    self._hps = hps
    self._vocab = vocab

  def _add_placeholders(self):
      """Add placeholders to the graph. These are entry points for any input data."""
      hps = self._hps

      # if FLAGS.run_method == 'auto-encoder':
      self._enc_text_batch = tf.placeholder(tf.int32, [hps.batch_size, hps.sc_max_enc_seq_len],
                                       name='enc_text_batch')
      self._enc_pos_batch = tf.placeholder(tf.int32, [hps.batch_size, hps.sc_max_enc_seq_len],
                                            name='enc_pos_batch')
      self._enc_dep_batch = tf.placeholder(tf.int32, [hps.batch_size, hps.sc_max_enc_seq_len],
                                            name='enc_dep_batch')

      self._enc_lens = tf.placeholder(tf.int32, [hps.batch_size], name='enc_sen_lens')
      self._rewards = tf.placeholder(tf.float32, [hps.batch_size], name = 'rewards')
      # self._weight = tf.placeholder(tf.float32, [hps.batch_size,hps.max_enc_steps], name = "weight")
      # self.score = tf.placeholder(tf.int32, name = 'score')


      # self._given_number = tf.placeholder(tf.int32, name = "given_number")
      # self._enc_padding_mask = tf.placeholder(tf.float32, [hps.batch_size, None], name='enc_padding_mask')


      self._dec_batch = tf.placeholder(tf.int32, [hps.batch_size, hps.sc_max_dec_seq_len], name='dec_batch')
      self._target_batch = tf.placeholder(tf.int32, [hps.batch_size, hps.sc_max_dec_seq_len], name='target_batch')
      self._dec_padding_mask = tf.placeholder(tf.float32, [hps.batch_size, hps.sc_max_dec_seq_len], name='dec_padding_mask')
      #self.reward = tf.placeholder(tf.float32, [hps.batch_size], name='reward')


  def _make_feed_dict(self, batch, just_enc=False):
      feed_dict = {}


      feed_dict[self._enc_text_batch] = batch.enc_text_batch
      feed_dict[self._enc_pos_batch] = batch.enc_pos_batch
      feed_dict[self._enc_dep_batch] = batch.enc_dep_batch
      feed_dict[self._enc_lens] = batch.enc_lens
      # feed_dict[self._enc_padding_mask] = batch.enc_padding_mask

      feed_dict[self._dec_batch] = batch.dec_batch
      #feed_dict[self.score] = batch.score
      #feed_dict[self._weight] = batch.weight
      feed_dict[self._rewards] = batch.rewards
      feed_dict[self._target_batch] = batch.target_batch
      feed_dict[self._dec_padding_mask] = batch.dec_padding_mask
      return feed_dict



  def _add_encoder(self, encoder_inputs, seq_len):
      with tf.variable_scope('encoder'):



          cell_fw1 = tf.contrib.rnn.LSTMCell(self._hps.hidden_dim, initializer=self.rand_unif_init, state_is_tuple=True)
          cell_fw2 = tf.contrib.rnn.LSTMCell(self._hps.hidden_dim, initializer=self.rand_unif_init, state_is_tuple=True)

          cell_bw1 = tf.contrib.rnn.LSTMCell(self._hps.hidden_dim, initializer=self.rand_unif_init, state_is_tuple=True)
          cell_bw2 = tf.contrib.rnn.LSTMCell(self._hps.hidden_dim, initializer=self.rand_unif_init, state_is_tuple=True)

          #cell_fw = tf.contrib.rnn.LSTMCell(self._hps.hidden_dim, initializer=self.rand_unif_init, state_is_tuple=True)
          (encoder_outputs, fw_st, bw_st) = tf.contrib.rnn.stack_bidirectional_dynamic_rnn([cell_fw1, cell_fw2], [cell_bw1, cell_bw2], encoder_inputs, dtype=tf.float32,
                                                         sequence_length=seq_len)
      return encoder_outputs, fw_st[-1], bw_st[-1]



  def _add_decoder(self, loop_function_max, input, encoder_output):  # input batch sequence dim
      hps = self._hps

      # input = tf.unstack(input, axis=1)

      cell = tf.contrib.rnn.LSTMCell(
          hps.hidden_dim,
          initializer=tf.random_uniform_initializer(-0.1, 0.1, seed=113),
          state_is_tuple=True)

      decoder_outputs_pretrain, _ = tf.contrib.legacy_seq2seq.rnn_decoder(
          input, self._dec_in_state,encoder_output,
          cell, loop_function=None
      )

      #scope = tf.variable_scope('attention_decoder', reuse= True)
      #tf.get_variable_scope().reuse_variables()

      decoder_outputs_max_generator, _ = tf.contrib.legacy_seq2seq.rnn_decoder(
          input, self._dec_in_state, encoder_output,
          cell, loop_function=loop_function_max,
      )

      decoder_outputs_pretrain = tf.stack(decoder_outputs_pretrain, axis=1)

      decoder_outputs_max_generator = tf.stack(decoder_outputs_max_generator, axis=1)

      # decoder_outputs_generator_rollout = tf.transpose(decoder_outputs_generator_rollout, [1, 0, 2])

      return decoder_outputs_pretrain, decoder_outputs_max_generator


  def add_decoder(self, embedding, emb_dec_inputs, encoder_output, vsize, hps):
        with tf.variable_scope('sc_negetive_decoder'):
            with tf.variable_scope('output_projection'):
                w = tf.get_variable(
                    'w', [hps.hidden_dim, 5], dtype=tf.float32,
                    initializer=tf.truncated_normal_initializer(stddev=1e-4))
                v = tf.get_variable(
                    'v', [5], dtype=tf.float32,
                    initializer=tf.truncated_normal_initializer(stddev=1e-4))
            # Add the decoder.


            with tf.variable_scope('decoder',):
                loop_function, loop_function_max = sample_output(
                    embedding, emb_dec_inputs, (w, v))
                decoder_outputs_pretrain, decoder_outputs_max_generator = self._add_decoder(
                    loop_function_max=loop_function_max, input=emb_dec_inputs, encoder_output=encoder_output )

                decoder_outputs_pretrain = tf.reshape(decoder_outputs_pretrain,
                                                      [hps.batch_size * hps.sc_max_dec_seq_len, hps.hidden_dim])


                decoder_outputs_pretrain = tf.nn.xw_plus_b(decoder_outputs_pretrain, w, v)

                self.decoder_outputs_pretrain = tf.reshape(decoder_outputs_pretrain,
                                                      [hps.batch_size, hps.sc_max_dec_seq_len, vsize])

                decoder_outputs_max_generator = tf.reshape(decoder_outputs_max_generator,
                                                           [hps.batch_size * hps.sc_max_dec_seq_len, hps.hidden_dim])

                decoder_outputs_max_generator = tf.nn.xw_plus_b(decoder_outputs_max_generator, w, v)

                self._max_best_output = tf.reshape(tf.argmax(decoder_outputs_max_generator, 1),
                                                   [hps.batch_size, hps.sc_max_dec_seq_len])
        return self.decoder_outputs_pretrain, self._max_best_output






  def _build_model(self):
      """Add the whole generator model to the graph."""
      hps = self._hps
      vsize = self._vocab.size()  # size of the vocabulary

      with tf.variable_scope('sc_seq2seq'):
          # Some initializers
          self.rand_unif_init = tf.random_uniform_initializer(-hps.rand_unif_init_mag, hps.rand_unif_init_mag, seed=123)
          self.trunc_norm_init = tf.truncated_normal_initializer(stddev=hps.trunc_norm_init_std)

          # Add embedding matrix (shared by the encoder and decoder inputs)
          with tf.variable_scope('embedding'):
              embedding = tf.get_variable('embedding', [vsize, hps.emb_dim], dtype=tf.float32,
                                          initializer=self.trunc_norm_init)


              decode_embedding = tf.get_variable('decode_embedding', [5, 10], dtype=tf.float32,
                                          initializer=self.trunc_norm_init)

              # embedding_score = tf.get_variable('embedding_score', [5, hps.hidden_dim], dtype=tf.float32, initializer=self.trunc_norm_init)

              emb_dec_inputs = tf.nn.embedding_lookup(decode_embedding,
                                                      self._dec_batch)  # list length max_dec_steps containing shape (batch_size, emb_size)
              emb_dec_inputs = tf.unstack(emb_dec_inputs, axis=1)

              emb_enc_inputs_text = tf.nn.embedding_lookup(embedding,
                                                      self._enc_text_batch)  # tensor with shape (batch_size, max_enc_steps, emb_size)

              emb_enc_inputs_pos = tf.nn.embedding_lookup(embedding,
                                                      self._enc_pos_batch)  # tensor with shape (batch_size, max_enc_steps, emb_size)

              emb_enc_inputs_dep = tf.nn.embedding_lookup(embedding,
                                                      self._enc_dep_batch)  # tensor with shape (batch_size, max_enc_steps, emb_size)


              emb_enc_inputs = tf.concat([emb_enc_inputs_text, emb_enc_inputs_pos, emb_enc_inputs_dep], axis = -1)


              encoder_output, fw_st, bw_st = self._add_encoder(emb_enc_inputs, self._enc_lens)

              w = tf.get_variable(
                  'w', [hps.hidden_dim*2, 5], dtype=tf.float32,
                  initializer=tf.truncated_normal_initializer(stddev=1e-4))
              v = tf.get_variable(
                  'v', [5], dtype=tf.float32,
                  initializer=tf.truncated_normal_initializer(stddev=1e-4))


              ntime_steps = tf.shape(encoder_output)[1]
              context_rep_flat = tf.reshape(encoder_output, [-1, hps.hidden_dim*2])

              decoder_outputs_pretrain = tf.matmul(context_rep_flat, w) + v
              self.decoder_outputs_pretrain = tf.reshape(decoder_outputs_pretrain, [-1, ntime_steps, 5])

              self.pred = tf.reshape(tf.argmax(decoder_outputs_pretrain, axis=-1),[-1, ntime_steps])

          loss = tf.contrib.seq2seq.sequence_loss(
              self.decoder_outputs_pretrain,
              self._target_batch,
              self._dec_padding_mask,
              average_across_timesteps=True,
              average_across_batch=False)

          # reward_loss = tf.contrib.seq2seq.sequence_loss(
          #     self.decoder_outputs_pretrain,
          #     self._target_batch,
          #     self._dec_padding_mask,
          #     average_across_timesteps=True,
          #     average_across_batch=False) * self.reward

          # Update the cost
          self._cost = tf.reduce_mean(loss)
          self._cost_re = tf.reduce_mean(loss*self._rewards)
          #self._reward_cost = tf.reduce_mean(reward_loss)
          self.optimizer = tf.train.AdagradOptimizer(self._hps.lr, initial_accumulator_value=self._hps.adagrad_init_acc)


  def _add_train_op(self):
      loss_to_minimize = self._cost
      tvars = tf.trainable_variables()
      gradients = tf.gradients(loss_to_minimize, tvars, aggregation_method=tf.AggregationMethod.EXPERIMENTAL_TREE)

      # Clip the gradients
      grads, global_norm = tf.clip_by_global_norm(gradients, self._hps.max_grad_norm)

      # Add a summary
      tf.summary.scalar('global_norm', global_norm)

      # Apply adagrad optimizer

      self._train_op = self.optimizer.apply_gradients(zip(grads, tvars), global_step=self.global_step,
                                                      name='train_step')

  # def _add_reward_train_op(self):
  #     loss_to_minimize = self._reward_cost
  #     tvars = tf.trainable_variables()
  #     gradients = tf.gradients(loss_to_minimize, tvars, aggregation_method=tf.AggregationMethod.EXPERIMENTAL_TREE)
  #
  #     # Clip the gradients
  #     grads, global_norm = tf.clip_by_global_norm(gradients, self._hps.max_grad_norm)
  #
  #     self._train_reward_op = self.optimizer.apply_gradients(zip(grads, tvars), global_step=self.global_step,
  #                                                            name='train_step')


  def build_graph(self):
      """Add the placeholders, model, global step, train_op and summaries to the graph"""
      with tf.device("/gpu:" + str(FLAGS.gpuid)):
          tf.logging.info('Building generator graph...')
          t0 = time.time()
          self._add_placeholders()
          self._build_model()
          self.global_step = tf.Variable(0, name='global_step', trainable=False)
          self._add_train_op()
          # self._add_reward_train_op()
          t1 = time.time()
          tf.logging.info('Time to build graph: %i seconds', t1 - t0)

  def run_pre_train_step(self, sess, batch):
      """Runs one training iteration. Returns a dictionary containing train op, summaries, loss, global_step and (optionally) coverage loss."""
      feed_dict = self._make_feed_dict(batch)
      to_return = {
          'train_op': self._train_op,
          'loss': self._cost,
          'global_step': self.global_step,
      }
      return sess.run(to_return, feed_dict)
  def run_rl_train_step(self, sess, batch):
      """Runs one training iteration. Returns a dictionary containing train op, summaries, loss, global_step and (optionally) coverage loss."""
      feed_dict = self._make_feed_dict(batch)
      to_return = {
          'train_op': self._train_op,
          'loss': self._cost_re,
          'global_step': self.global_step,
      }
      return sess.run(to_return, feed_dict)




  def max_generator(self,sess, batch):
      feed_dict = self._make_feed_dict(batch)
      to_return = {
          'generated': self.pred,
      }
      return sess.run(to_return, feed_dict)


