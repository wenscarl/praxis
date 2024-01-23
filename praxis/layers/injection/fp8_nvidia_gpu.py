# coding=utf-8
# Copyright 2022 The Pax Authors.
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

# coding=utf-8
# Copyright 2023 The Pax Authors.
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

"""Op wrappers to support FP8 GEMMs."""

from functools import partial

from flax.linen import fp8_ops
from jax import custom_vjp
from jax import lax
from jax import numpy as jnp
from praxis import base_layer
from praxis import layers
from praxis import pax_fiddle
from praxis import pytypes


class Fp8EinsumOp(base_layer.BaseLayer):
  """Wrapper around jnp.einsum used in standard Pax layers."""

  amax_history_length: int = 1024

  def setup(self) -> None:
    OVERWRITE_WITH_GRADIENT = (
        base_layer.WeightHParamsCollection.OVERWRITE_WITH_GRADIENT
    )
    scale_args = {
        'shape': [1],
        'init': base_layer.WeightInit.Constant(1.0),
        'dtype': jnp.float32,
        'mesh_shape': self.mesh_shape,
        'tensor_split_dims_mapping': None,
        'collections': [OVERWRITE_WITH_GRADIENT],
    }
    amax_history_args = {
        'shape': [self.amax_history_length],
        'init': base_layer.WeightInit.Constant(0.0),
        'dtype': jnp.float32,
        'mesh_shape': self.mesh_shape,
        'tensor_split_dims_mapping': None,
        'collections': [OVERWRITE_WITH_GRADIENT],
    }
    self.create_variable(
        'input_amax_history', base_layer.WeightHParams(**amax_history_args)
    )
    self.create_variable(
        'kernel_amax_history', base_layer.WeightHParams(**amax_history_args)
    )
    self.create_variable(
        'output_grad_amax_history',
        base_layer.WeightHParams(**amax_history_args),
    )

    self.create_variable('input_scale', base_layer.WeightHParams(**scale_args))
    self.create_variable('kernel_scale', base_layer.WeightHParams(**scale_args))
    self.create_variable(
        'output_grad_scale', base_layer.WeightHParams(**scale_args)
    )

  def __call__(self, equation: str, *args: pytypes.JTensor, use_amax_history=True) -> pytypes.JTensor:
    assert len(args) == 2
    x = args[0]
    k = args[1]

    comp_dtype = self.fprop_dtype
    assert (
        k.dtype == comp_dtype
    ), f'k dtype has to be {comp_dtype}, but got {k.dtype}'
    x = jnp.asarray(x, comp_dtype)
    
    theta = self.theta
    if use_amax_history:
        x_qdq = fp8_ops.in_qdq(
            comp_dtype, x, theta.input_scale, theta.input_amax_history
        )
        k_qdq = fp8_ops.in_qdq(
            comp_dtype, k, theta.kernel_scale, theta.kernel_amax_history
        )
        y_qdq = jnp.einsum(
            equation, x_qdq, k_qdq, _dot_general=fp8_ops.dot_general_with_precision
        )
        y = fp8_ops.out_qdq(
            comp_dtype,
            y_qdq,
            theta.output_grad_scale,
            theta.output_grad_amax_history,
        )
    else:
      x_qdq = fp8_ops.quantize_dequantize(x, jnp.float8_e4m3fn, theta.input_scale, comp_dtype)
      k_qdq = fp8_ops.quantize_dequantize(k, jnp.float8_e4m3fn, theta.kernel_scale, comp_dtype)
      y = jnp.einsum(equation, x_qdq, k_qdq, _dot_general=fp8_ops.dot_general_with_precision)

    return y
