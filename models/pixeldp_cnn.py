# Copyright 2016 The Pixeldp Authors. All Rights Reserved.
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
# Based on https://github.com/tensorflow/models/tree/master/research/resnet

"""CNN model.
"""
from models import pixeldp
from collections import namedtuple

import numpy as np
import tensorflow as tf
import six
import math
import utils

from tensorflow.python.training import moving_averages

class Model(pixeldp.Model):
    """CNN model."""

    def __init__(self, hps, images, labels, mode):
        """ResNet constructor.

        Args:
          hps: Hyperparameters.
          images: Batches of images. [batch_size, image_size, image_size, 3]
          labels: Batches of labels. [batch_size, num_classes]
          mode: One of 'train' and 'eval'.
        """
        pixeldp.Model.__init__(self, hps, images, labels, mode)

    def _build_model(self):
        """Build the core model within the graph."""
        assert(self.hps.noise_after_n_layers <= 2)

        input_layer = self.images
        self.labels = self.labels

        with tf.variable_scope('im_dup'):
            # Duplicate images to get multiple draws from the DP label
            # ditribution (each duplicate gets an independent noise draw
            # before going through the rest of the network).
            ones = tf.ones([len(input_layer.get_shape())-1], dtype=tf.int32)
            x = tf.tile(input_layer, tf.concat([[self.hps.n_draws], ones], axis=0))

        x = self._maybe_add_noise_layer(x, sensitivity_norm=self.hps.sensitivity_norm,
                sensitivity_control_scheme=self.hps.sensitivity_control_scheme,
                position=0)

        with tf.variable_scope('init'):
            filter_size = 5
            in_filters  = x.get_shape()[-1]
            out_filters = 32
            stride      = 2
            strides     = self._stride_arr(stride)

            x = self._conv(
                "init_conv", x, filter_size, in_filters, out_filters, strides,
                is_pre_noise=self.hps.noise_after_n_layers >= 1,
                layer_sensivity_bound=self.hps.layer_sensitivity_bounds[0],
                sensitivity_control_scheme=self.hps.sensitivity_control_scheme
            )

        if not self.hps.noise_after_activation:
            x = self._relu(x, self.hps.relu_leakiness)

        x = self._maybe_add_noise_layer(x, sensitivity_norm=self.hps.sensitivity_norm,
                sensitivity_control_scheme=self.hps.sensitivity_control_scheme,
                position=1)

        if self.hps.noise_after_activation:
            x = self._relu(x, self.hps.relu_leakiness)

        x = self._conv(
            "conv2", x, 5, out_filters, 64, self._stride_arr(2),
            is_pre_noise=self.hps.noise_after_n_layers >= 2,
            layer_sensivity_bound=self.hps.layer_sensitivity_bounds[0],
            sensitivity_control_scheme=self.hps.sensitivity_control_scheme
        )

        if not self.hps.noise_after_activation:
            x = self._relu(x, self.hps.relu_leakiness)

        x = self._maybe_add_noise_layer(x, sensitivity_norm=self.hps.sensitivity_norm,
                sensitivity_control_scheme=self.hps.sensitivity_control_scheme,
                position=2)

        if self.hps.noise_after_activation:
            x = self._relu(x, self.hps.relu_leakiness)

        with tf.variable_scope('dense'):
            x = self._fully_connected(x, 1024)
            x = self._relu(x, self.hps.relu_leakiness)

        with tf.variable_scope('logit'):
            logits = self._fully_connected(x, self.hps.num_classes)
            self.predictions = tf.nn.softmax(logits)

        with tf.variable_scope('label_dup'):
            ones   = tf.ones([len(self.labels.get_shape())-1], dtype=tf.int32)
            labels = tf.tile(self.labels, tf.concat([[self.hps.n_draws], ones], axis=0))

        with tf.variable_scope('costs'):
            xent = tf.nn.softmax_cross_entropy_with_logits(
                logits=logits, labels=labels)
            self.cost = tf.reduce_mean(xent, name='xent')
            self.cost += self._decay()

            tf.summary.scalar('cost', self.cost)
