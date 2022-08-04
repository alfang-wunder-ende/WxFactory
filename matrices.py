import numpy
import numpy.linalg
import math
import sympy
import sys

from cubed_sphere import cubed_sphere

# from cubed_sphere import cubed_sphere

class DFR_operators:
   def __init__(self, grd, filter_apply=False, filter_order=8, filter_cutoff=0.25):
      '''Initialize the Direct Flux Reconstruction operators
      
      This initializes the DFR operators (matrices) based on input grid parameters.  The relevant internal matrices are:
         * The extrapolation matrices, `extrap_west`, `extrap_east`, `extrap_south`, `extrap_north`, `extrap_down`, and
           `extrap_up`.

      Parameters
      ----------
      grd : cubed_sphere
         Underlying grid, which must define `solutionPoints`, `solutionPoints_sym`, `extension`, and `extension_sym` as mmeber variables
      filter_apply : bool
         Whether to apply an exponential filter in defininng the differential operators
      filter_order : int
         If applied, what order of exponential to use for the filter
      filter_cutoff : float
         If applied, at what relative wavenumber (0 < cutoff < 1) to begin applying the filter
      '''
      
      self.extrap_west = lagrangeEval(grd.solutionPoints_sym, -1)
      self.extrap_east = lagrangeEval(grd.solutionPoints_sym,  1)

      self.extrap_south = lagrangeEval(grd.solutionPoints_sym, -1)
      self.extrap_north = lagrangeEval(grd.solutionPoints_sym,  1)

      self.extrap_down = lagrangeEval(grd.solutionPoints_sym, -1)
      self.extrap_up   = lagrangeEval(grd.solutionPoints_sym,  1)

      if filter_apply:
         self.V = vandermonde(grd.extension)
         self.invV = inv(self.V)
         N = len(grd.extension)-1
         Nc = math.floor(filter_cutoff * N)
         self.filter = filter_exponential(N, Nc, filter_order, self.V, self.invV)

      diff = diffmat(grd.extension_sym)

      if filter_apply:
         self.diff_ext = ( self.filter @ diff ).astype(float)
         self.diff_ext[numpy.abs(self.diff_ext) < 1e-20] = 0.
      else:
         self.diff_ext = diff

      # if check_skewcentrosymmetry(self.diff_ext) is False:
      #    print('Something horribly wrong has happened in the creation of the differentiation matrix')
      #    exit(1)

      # Force matrices to be in C-contiguous order
      self.diff_solpt = numpy.ascontiguousarray( self.diff_ext[1:-1, 1:-1] )
      self.correction = numpy.ascontiguousarray( numpy.column_stack((self.diff_ext[1:-1,0], self.diff_ext[1:-1,-1])) )

      self.diff_solpt_tr = self.diff_solpt.T.copy()
      self.correction_tr = self.correction.T.copy()

      # Ordinary differentiation matrices (used only in diagnostic calculations)
      self.diff = diffmat(grd.solutionPoints)
      self.diff_tr = self.diff.T

      self.quad_weights = numpy.outer(grd.glweights, grd.glweights)

   def comma_i(self, field_interior, border_i, grid: cubed_sphere):
      '''Take a partial derivative along the i-index
      
      This method takes the partial derivative of an input field, potentially consisting of several
      variables, along the `i` index.  This derivative is performed with respect to the canonical element,
      so it contains no corrections for the problem geometry.

      Parameters
      ----------
      field_interior : numpy.array
         The element-interior values of the variable(s) to be differentiated.  This should have
         a shape of `(numvars,npts_z,npts_y,npts_x)`, respecting the prevailing parallel decomposition.
      border_i : numpy.array
         The element-boundary values of the fields to be differentiated, along the i-axis.  This should
         have a shape of `(numvars,npts_z,npts_y,nels_x,2)`, with [:,0] being the leftmost boundary
         (minimal `i`), and [:,1] being the rightmost boundary (maximal `i`)
      grid : cubed_sphere
         Grid-defining class, used here solely to provide the canonical definition of the local
         computational region.
      '''
      # Create an empty array for output
      output = numpy.empty_like(field_interior)

      # Create views of the input arrays for reshaping, in order to express the differentiation as
      # a set of matrix multiplications

      field_view = field_interior.view()
      border_i_view = border_i.view()

      # Reshape to a flat view.  Assigning to array.shape will raise an exception if the new shape would
      # require a memory copy; this implicitly ensures that the input arrays are fully contiguous in
      # memory.
      field_view.shape = (-1, grid.nbsolpts)
      border_i_view.shape = (-1,2)
      output.shape = field_view.shape

      # Perform the matrix transposition
      output[:] = field_view @ self.diff_solpt_tr + border_i_view @ self.correction_tr
      # print(grid.ptopo.rank, field_view[:2,:], '\n', border_i_view[:2,:],'\n',output[:2,:])

      # Reshape the output array back to its canonical extents
      output.shape = field_interior.shape

      return output

   def extrapolate_i(self, field_interior, grid):
      '''Compute the i-border values along each element of field_interior

      This method extrapolates the variables in `field_interior` to the boundary along
      the i-dimension (last index), using the `extrap_west` and `extrap_east` matrices.

      Parameters
      ----------
      field_interior : numpy.array
         The element-interior values of the variable(s) to be differentiated.  This should have
         a shape of `(numvars,npts_z,npts_y,npts_x)`, respecting the prevailing parallel decomposition.
      grid : cubed_sphere
         Grid-defining class, used here solely to provide the canonical definition of the local
         computational region.'''

      # Array shape for the i-border of a single variable, based on the grid decomposition
      border_shape = (grid.nb_elements_x1,2)
      # Number of variables we're extending
      nbvars = numpy.prod(field_interior.shape) // (grid.ni)

      # Create an array for the output
      border = numpy.empty((nbvars,) + border_shape, dtype=field_interior.dtype)
      # Reshape to the from required for matrix multiplication
      border.shape = (-1,2)

      # Create an array view of the interior, reshaped for matrix multiplication
      field_interior_view = field_interior.view()
      field_interior_view.shape = (-1,grid.nbsolpts)

      # Perform the extrapolations via matrix multiplication
      border[:,0] = field_interior_view @ self.extrap_west
      border[:,1] = field_interior_view @ self.extrap_east

      border.shape = tuple(field_interior.shape[0:-1]) + border_shape
      return border

   def comma_j(self, field_interior, border_j, grid):
      '''Take a partial derivative along the j-index
      
      This method takes the partial derivative of an input field, potentially consisting of several
      variables, along the `j` index.  This derivative is performed with respect to the canonical element,
      so it contains no corrections for the problem geometry.

      Parameters
      ----------
      field_interior : numpy.array
         The element-interior values of the variable(s) to be differentiated.  This should have
         a shape of `(numvars,npts_z,npts_y,npts_x)`, respecting the prevailing parallel decomposition.
      border_j : numpy.array
         The element-boundary values of the fields to be differentiated, along the i-axis.  This should
         have a shape of `(numvars,npts_z,nels_y,2,npts_x)`, with [:,0,:] being the southmost boundary
         (minimal `j`), and [:,1,:] being the north boundary (maximal `j`)
      grid : cubed_sphere
         Grid-defining class, used here solely to provide the canonical definition of the local
         computational region.
      '''
      # Create an empty array for output
      output = numpy.empty_like(field_interior)
      # Compute the number of variables we're differentiating, including number of levels
      nbvars = numpy.prod(output.shape) // (grid.ni * grid.nj)

      # Create views of the input arrays for reshaping, in order to express the differentiation as
      # a set of matrix multiplications

      field_view = field_interior.view()
      border_j_view = border_j.view()

      # Reshape to a flat view.  Assigning to array.shape will raise an exception if the new shape would
      # require a memory copy; this implicitly ensures that the input arrays are fully contiguous in
      # memory.
      field_view.shape = (nbvars*grid.nb_elements_x2,grid.nbsolpts,grid.ni)
      border_j_view.shape = (nbvars*grid.nb_elements_x2,2,grid.ni)
      output.shape = field_view.shape

      # Perform the matrix transposition
      output[:] = self.diff_solpt @ field_view + self.correction @ border_j_view

      # Reshape the output array back to its canonical extents
      output.shape = field_interior.shape

      return output

   def extrapolate_j(self, field_interior, grid):
      '''Compute the j-border values along each element of field_interior

      This method extrapolates the variables in `field_interior` to the boundary along
      the j-dimension (second last index), using the `extrap_south` and `extrap_north` matrices.

      Parameters
      ----------
      field_interior : numpy.array
         The element-interior values of the variable(s) to be differentiated.  This should have
         a shape of `(numvars,npts_z,npts_y,npts_x)`, respecting the prevailing parallel decomposition.
         To allow for differentiation of 2D objects, npts_z can be one.
      grid : cubed_sphere
         Grid-defining class, used here solely to provide the canonical definition of the local
         computational region.'''

      # Array shape for the i-border of a single variable, based on the grid decomposition
      border_shape = (grid.nb_elements_x2,2,
                      grid.ni)
      # Number of variables times number of vertical levels we're extending
      nbvars = numpy.prod(field_interior.shape) // (grid.ni * grid.nj)

      # Create an array for the output
      border = numpy.empty((nbvars,) + border_shape, dtype=field_interior.dtype)
      # Reshape to the from required for matrix multiplication
      border.shape = (-1,2,grid.ni)

      # Create an array view of the interior, reshaped for matrix multiplication
      field_interior_view = field_interior.view()
      field_interior_view.shape = (-1,grid.nbsolpts,grid.ni)

      # Perform the extrapolations via matrix multiplication
      # print(border[:,0,:].shape, field_interior_view.shape, self.extrap_south.T.shape)
      border[:,0,:] = (self.extrap_south @ field_interior_view)
      border[:,1,:] = (self.extrap_north @ field_interior_view)

      # field_interior.shape[0:-2] is (nbvars,nk) for many 3D fields, (nbvars,) for many 2D fields,
      # (nk) for a single 3D field, and () for a single 2D field.
      border.shape = tuple(field_interior.shape[0:-2]) + border_shape
      return border

   def comma_k(self, field_interior, border_k, grid):
      '''Take a partial derivative along the k-index
      
      This method takes the partial derivative of an input field, potentially consisting of several
      variables, along the `k` index.  This derivative is performed with respect to the canonical element,
      so it contains no corrections for the problem geometry.

      Parameters
      ----------
      field_interior : numpy.array
         The element-interior values of the variable(s) to be differentiated.  This should have
         a shape of `(numvars,npts_z,npts_y,npts_x)`, respecting the prevailing parallel decomposition.
      border_k : numpy.array
         The element-boundary values of the fields to be differentiated, along the i-axis.  This should
         have a shape of `(numvars,nels_z,2,npts_y,npts_x)`, with [:,0,:] being the downmost boundary
         (minimal `k`), and [:,1,:] being the upmost boundary (maximal `k`)
      grid : cubed_sphere
         Grid-defining class, used here solely to provide the canonical definition of the local
         computational region.
      '''
      # Create an empty array for output
      output = numpy.empty_like(field_interior)
      # Compute the number of variables we're differentiating
      nbvars = numpy.prod(output.shape) // (grid.ni * grid.nj * grid.nk)

      # Create views of the input arrays for reshaping, in order to express the differentiation as
      # a set of matrix multiplications

      field_view = field_interior.view()
      border_k_view = border_k.view()

      # Reshape to a flat view.  Assigning to array.shape will raise an exception if the new shape would
      # require a memory copy; this implicitly ensures that the input arrays are fully contiguous in
      # memory.
      field_view.shape = (nbvars*grid.nb_elements_x3,grid.nbsolpts,grid.ni*grid.nj)
      border_k_view.shape = (nbvars*grid.nb_elements_x3,2,grid.ni*grid.nj)
      output.shape = field_view.shape

      # Perform the matrix transposition
      output[:] = self.diff_solpt @ field_view + self.correction @ border_k_view

      # Reshape the output array back to its canonical extents
      output.shape = field_interior.shape

      return output

   def extrapolate_k(self, field_interior, grid):
      '''Compute the k-border values along each element of field_interior

      This method extrapolates the variables in `field_interior` to the boundary along
      the k-dimension (third last index), using the `extrap_down` and `extrap_up` matrices.

      Parameters
      ----------
      field_interior : numpy.array
         The element-interior values of the variable(s) to be differentiated.  This should have
         a shape of `(numvars,npts_z,npts_y,npts_x)`, respecting the prevailing parallel decomposition.
      grid : cubed_sphere
         Grid-defining class, used here solely to provide the canonical definition of the local
         computational region.'''

      # Array shape for the i-border of a single variable, based on the grid decomposition
      border_shape = (grid.nb_elements_x3,2,
                      grid.nj,
                      grid.ni)
      # Number of variables we're extending
      nbvars = numpy.prod(field_interior.shape) // (grid.ni * grid.nj * grid.nk)

      # Create an array for the output
      border = numpy.empty((nbvars,) + border_shape, dtype=field_interior.dtype)
      # Reshape to the from required for matrix multiplication
      border.shape = (-1,2,grid.ni*grid.nj)

      # Create an array view of the interior, reshaped for matrix multiplication
      field_interior_view = field_interior.view()
      field_interior_view.shape = (-1,grid.nbsolpts,grid.ni*grid.nj)

      # Perform the extrapolations via matrix multiplication
      border[:,0,:] = (self.extrap_down @ field_interior_view)
      border[:,1,:] = (self.extrap_up @ field_interior_view)

      if (nbvars > 1):
         border.shape = (nbvars,) + border_shape
      else:
         border.shape = border_shape   
      return border





def lagrangeEval(points, newPt):
   M = len(points)
   x = sympy.symbols('x')
   l = numpy.zeros_like(points)
   if M == 1: 
      l[0] = 1 # Constant
   else:
      for i in range(M):
         l[i] = Lagrange_poly(x, M-1, i, points).evalf(subs={x: newPt}, n=20)
   return l.astype(float)


def diffmat(points):
   M = len(points)
   D = numpy.zeros((M,M))

   x = sympy.symbols('x')
   for i in range(M):
      dL = sympy.diff( Lagrange_poly(x, M-1, i, points) )
      for j in range(M):
         if i != j:
            D[j,i] = dL.subs(x, points[j])
      D[i, i] = dL.subs(x, points[i])

   return D


def Lagrange_poly(x,order,i,xi):
    index = list(range(order+1))
    index.pop(i)
    return sympy.prod([(x-xi[j])/(xi[i]-xi[j]) for j in index])

def lebesgue(points):
   M = len(points)
   eval_set = numpy.linspace(-1,1,M)
   x = sympy.symbols('x')
   l = 0
   for i in range(M):
      l = l + sympy.Abs( Lagrange_poly(x,M-1,i,points) )
   return [l.subs(x, eval_set[i]) for i in range(M)]

def vandermonde(x):
   r"""
   Initialize the 1D Vandermonde matrix, \(\mathcal{V}_{ij}=P_j(x_i)\)
   """
   N = len(x)

   V = numpy.zeros((N, N), dtype=object)
   y = sympy.symbols('y')
   for j in range(N):
      for i in range(N):
         V[i, j] = sympy.legendre(j, y).evalf(subs={y: x[i]}, n=30, chop=True)

   return V


def remesh_operator(src_points, target_points):
   src_nbsolpts = len(src_points)
   target_nbsolpts = len(target_points)

   interp = numpy.zeros((target_nbsolpts, src_nbsolpts))
   for i in range(target_nbsolpts):
      interp[i,:] = lagrangeEval(src_points, target_points[i])
   return interp


def filter_exponential(N, Nc, s, V, invV):
   r"""
   Create an exponential filter matrix that can be used to filter out
   high-frequency noise.

   The filter matrix \(\mathcal{F}\) is defined as \(\mathcal{F}=
   \mathcal{V}\Lambda\mathcal{V}^{-1}\) where the diagonal matrix,
   \(\Lambda\) has the entries \(\Lambda_{ii}=\sigma(i-1)\) for
   \(i=1,\ldots,n+1\) and the filter function, \(\sigma(i)\) has the form
   \[
      \sigma(i) =
         \begin{cases}
            1 & 0\le i\le n_c \\
            e^{-\alpha\left (\frac{i-n_c}{n-n_c}\right )^s} & n_c<i\le n.
      \end{cases}
   \]
   Here \(\alpha=-\log(\epsilon_M)\), where \(\epsilon_M\) is the machine
   precision in working precision, \(n\) is the order of the element,
   \(n_c\) is a cutoff, below which the low modes are left untouched and
   \(s\) (has to be even) is the order of the filter.

   Inputs:
      N : The order of the element.
      Nc : The cutoff, below which the low modes are left untouched.
      s : The order of the filter.
      V : The Vandermonde matrix, \(\mathcal{V}\).
      invV : The inverse of the Vandermonde matric, \(\mathcal{V}^{-1}\).

   Outputs:
      F: The return value is the filter matrix, \(\mathcal{F}\).
   """

   n_digit = 30

   alpha = -sympy.log(sympy.Float(numpy.finfo(float).eps, n_digit))

   F = numpy.identity(N+1, dtype=object)
   for i in range(Nc, N+1):
      t = sympy.Rational((i-Nc), (N-Nc))
      F[i,i] = sympy.exp(-alpha*t**s)

   F = V @ F @ invV

   return F


def check_skewcentrosymmetry(m):
   n,n = m.shape
   middle_row = 0

   if n % 2 == 0:
      middle_row = int(n / 2)
   else:
      middle_row = int(n / 2 + 1)

      if m[middle_row-1, middle_row-1] != 0.:
         print()
         print('When the order is odd, the central entry of a skew-centrosymmetric matrix must be zero.\nActual value is', m[middle_row-1, middle_row-1])
         return False

   for i in range(middle_row):
      for j in range(n):
         if (m[i, j] != -m[n-i-1, n-j-1]):
            print('Non skew-centrosymmetric entries detected:', (m[i, j], m[n-i-1, n-j-1]))
            return False

   return True


# Borrowed from Galois:
# https://github.com/mhostetter/galois
def inv(A):
    if not (A.ndim == 2 and A.shape[0] == A.shape[1]):
        raise numpy.linalg.LinAlgError(f"Argument `A` must be square, not {A.shape}.")
    field = type(A)
    n = A.shape[0]
    I = numpy.eye(n, dtype=A.dtype)

    # Concatenate A and I to get the matrix AI = [A | I]
    AI = numpy.concatenate((A, I), axis=-1)

    # Perform Gaussian elimination to get the reduced row echelon form AI_rre = [I | A^-1]
    AI_rre = row_reduce(AI, ncols=n)

    # The rank is the number of non-zero rows of the row reduced echelon form
    rank = numpy.sum(~numpy.all(AI_rre[:,0:n] == 0, axis=1))
    if not rank == n:
        raise numpy.linalg.LinAlgError(f"Argument `A` is singular and not invertible because it does not have full rank of {n}, but rank of {rank}.")

    A_inv = AI_rre[:,-n:]

    return A_inv


def row_reduce(A, ncols=None):
    if not A.ndim == 2:
        raise ValueError(f"Only 2-D matrices can be converted to reduced row echelon form, not {A.ndim}-D.")

    ncols = A.shape[1] if ncols is None else ncols
    A_rre = A.copy()
    p = 0  # The pivot

    for j in range(ncols):
        # Find a pivot in column `j` at or below row `p`
        idxs = numpy.nonzero(A_rre[p:,j])[0]
        if idxs.size == 0:
            continue
        i = p + idxs[0]  # Row with a pivot

        # Swap row `p` and `i`. The pivot is now located at row `p`.
        A_rre[[p,i],:] = A_rre[[i,p],:]

        # Force pivot value to be 1
        A_rre[p,:] /= A_rre[p,j]

        # Force zeros above and below the pivot
        idxs = numpy.nonzero(A_rre[:,j])[0].tolist()
        idxs.remove(p)
        A_rre[idxs,:] -= numpy.multiply.outer(A_rre[idxs,j], A_rre[p,:])

        p += 1
        if p == A_rre.shape[0]:
            break

    return A_rre
