import math
from typing import Callable, Tuple

import numpy
import cupy

class MatvecOp:
   def __init__(self, matvec: Callable[[numpy.ndarray], numpy.ndarray], dtype, shape: Tuple) -> None:
      self.matvec = matvec
      self.dtype = dtype
      self.shape = shape
      self.size = math.prod([i for i in shape])

   def __call__(self, vec: numpy.ndarray) -> numpy.ndarray:
      return self.matvec(vec)

class MatvecOpBasic(MatvecOp):
   def __init__(self, dt: float, Q: numpy.ndarray, rhs_vec: numpy.ndarray, rhs_handle: Callable) -> None:
      super().__init__(
         lambda vec: matvec_fun(vec, dt, Q, rhs_vec, rhs_handle),
         Q.dtype, Q.shape)

def matvec_fun(vec: numpy.ndarray, dt: float, Q: numpy.ndarray, rhs: numpy.ndarray, rhs_handle, method='complex') \
   -> numpy.ndarray:
   xp = cupy.get_array_module(vec)
   if method == 'complex':
      # Complex-step approximation
      epsilon = math.sqrt(numpy.finfo(float).eps)
      Qvec = Q + 1j * epsilon * xp.reshape(vec, Q.shape)
      jac = dt * (rhs_handle(Qvec) / epsilon).imag
   else:
      # Finite difference approximation
      epsilon = math.sqrt(numpy.finfo(numpy.float32).eps)
      Qvec = Q + epsilon * xp.reshape(vec, Q.shape)
      jac = dt * ( rhs_handle(Qvec) - rhs) / epsilon

   return jac.flatten()

class MatvecOpRat(MatvecOp):
   def __init__(self, dt: float, Q: numpy.ndarray, rhs_vec: numpy.ndarray, rhs_handle: Callable) -> None:
      super().__init__(
         lambda vec: matvec_rat(vec, dt, Q, rhs_vec, rhs_handle),
         Q.dtype, Q.shape)

def matvec_rat(vec: numpy.ndarray, dt: float, Q: numpy.ndarray, rhs: numpy.ndarray, rhs_handle) -> numpy.ndarray:

   epsilon = math.sqrt(numpy.finfo(numpy.float32).eps)
   Qvec = Q + epsilon * numpy.reshape(vec, Q.shape)
   jac = dt * ( rhs_handle(Qvec) - rhs) / epsilon

   return vec - 0.5 * jac.flatten()
