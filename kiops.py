import math
import numpy
import mpi4py.MPI
import scipy.linalg

"""
   kiops(tstops, A, u; kwargs...) -> (w, stats)

Evaluate a linear combinaton of the ``φ`` functions evaluated at ``tA`` acting on
vectors from ``u``, that is

```math
  w(i) = φ_0(t[i] A) u[:, 1] + φ_1(t[i] A) u[:, 2] + φ_2(t[i] A) u[:, 3] + ...
```

The size of the Krylov subspace is changed dynamically during the integration.
The Krylov subspace is computed using the incomplete orthogonalization method.

Arguments:
  - `τ_out`    - Array of `τ_out`
  - `A`        - the matrix argument of the ``φ`` functions
  - `u`        - the matrix with columns representing the vectors to be multiplied by the ``φ`` functions

Optional arguments:
  - `tol`      - the convergence tolerance required (default: 1e-7)
  - `mmin`, `mmax` - let the Krylov size vary between mmin and mmax (default: 10, 128)
  - `m`        - an estimate of the appropriate Krylov size (default: mmin)
  - `iop`      - length of incomplete orthogonalization procedure (default: 2)
  - `ishermitian` -  whether ``A`` is Hermitian (default: ishermitian(A))
  - `task1`     - if true, divide the result by 1/T**p

Returns:
  - `w`      - the linear combination of the ``φ`` functions evaluated at ``tA`` acting on the vectors from ``u``
  - `stats[1]` - number of substeps
  - `stats[2]` - number of rejected steps
  - `stats[3]` - number of Krylov steps
  - `stats[4]` - number of matrix exponentials
  - `stats[5]` - Error estimate
  - `stats[6]` - the Krylov size of the last substep

`n` is the size of the original problem
`p` is the highest index of the ``φ`` functions

References:
* Gaudreault, S., Rainwater, G. and Tokman, M., 2018. KIOPS: A fast adaptive Krylov subspace solver for exponential integrators. Journal of Computational Physics. Based on the PHIPM and EXPMVP codes (http://www1.maths.leeds.ac.uk/~jitse/software.html). https://gitlab.com/stephane.gaudreault/kiops.
* Niesen, J. and Wright, W.M., 2011. A Krylov subspace method for option pricing. SSRN 1799124
* Niesen, J. and Wright, W.M., 2012. Algorithm 919: A Krylov subspace algorithm for evaluating the ``φ``-functions appearing in exponential integrators. ACM Transactions on Mathematical Software (TOMS), 38(3), p.22
"""
def kiops(τ_out, A, u, tol = 1e-7, m_init = 10, mmin = 10, mmax = 128, iop = 2, task1 = False):

   n, ppo = u.shape
   p = ppo - 1

   if p == 0:
      p = 1
      # Add extra column of zeros
      u = numpy.column_stack((u, numpy.zeros(len(u))))

   # We only allow m to vary between mmin and mmax
   m = max(mmin, min(m_init, mmax))

   # Preallocate matrix
   V = numpy.zeros((n + p, mmax + 1))
   H = numpy.zeros((mmax + 1, mmax + 1))

   step    = 0
   krystep = 0
   ireject = 0
   reject  = 0
   exps    = 0
   sgn     = numpy.sign(τ_out[-1])
   τ_now   = 0.0
   τ_end   = abs(τ_out[-1])
   happy   = False
   j       = 0

   conv    = 0.0

   numSteps = len(τ_out)

   # Initial condition
   w     = numpy.zeros((n, numSteps))
   w_aug = numpy.zeros(p)
   w[:, 0] = u[:, 0]

   # compute the 1-norm of u
   local_sum = numpy.sum(abs(u[:, 1:]), axis=0)
   global_sum = mpi4py.MPI.COMM_WORLD.allreduce(local_sum) # TODO : tester avec p>1
   normU = numpy.amax(global_sum)

   # Normalization factors
   if ppo > 1 and normU > 0:
      ex = math.ceil(math.log2(normU))
      nu = 2**(-ex)
      mu = 2**(ex)
   else:
      nu = 1.0
      mu = 1.0

   # Flip the rest of the u matrix
   u_flip = nu * numpy.fliplr(u[:, 1:])

   # Compute and initial starting approximation for the step size
   τ = τ_end

   # Setting the safety factors and tolerance requirements
   if τ_end > 1:
      γ = 0.2
      γ_mmax = 0.1
   else:
      γ = 0.9
      γ_mmax = 0.6

   delta = 1.4

   # Used in the adaptive selection
   oldm = -1; oldτ = math.nan; ω = math.nan
   orderold = True; kestold = True

   l = 0

   while τ_now < τ_end:

      # Compute necessary starting information
      if j == 0:
         # Update the last part of w
         for k in range(p-1):
            i = p - k + 1
            w_aug[k] = (τ_now**i) / math.factorial(i) * mu

         w_aug[p-1] = mu

         # Initialize the matrices V and H
         H[:,:] = 0.0

         # Normalize initial vector (this norm is nonzero)
         β = math.sqrt( w[:,l] @ w[:,l] + w_aug @ w_aug )

         # The first Krylov basis vector
         V[0:n, j]   = (1/β) * w[:,l]
         V[n:n+p, j] = (1/β) * w_aug

      # Incomplete orthogonalization process
      while j < m:

         j = j + 1

         # Augmented matrix - vector product
         V[0:n    , j] = A( V[0:n, j-1] ) + u_flip @ V[n:n+p, j-1]
         V[n:n+p-1, j] = V[n+1:n+p, j-1]
         V[-1     , j] = 0.

         # Modified Gram-Schmidt
         for i in range(max(0, j - iop), j):
            local_sum = V[:, i] @ V[:, j]
            H[i, j-1] = mpi4py.MPI.COMM_WORLD.allreduce(local_sum)
            V[:, j] = V[:, j] - H[i, j-1] * V[:, i]

         local_sum = V[:, j] @ V[:, j]
         nrm = numpy.sqrt( mpi4py.MPI.COMM_WORLD.allreduce(local_sum) )

         # Happy breakdown
         if nrm < tol:
            happy = True
            break

         H[j, j-1] = nrm
         V[:, j]   = (1.0 / nrm) * V[:, j]

         krystep += 1


      # To obtain the phi_1 function which is needed for error estimate
      H[0, j] = 1.0

      # Save h_j+1,j and remove it temporarily to compute the exponential of H
      nrm       = H[j, j-1]
      H[j, j-1] = 0.0

      # Compute the exponential of the augmented matrix
      F = scipy.linalg.expm(sgn * τ * H[0:j + 1, 0:j + 1])
      exps += 1

      # Restore the value of H_{m+1,m}
      H[j, j-1] = nrm

      if happy:
         # Happy breakdown wrap up
         ω     = 0.
         err   = 0.
         τ_new = min(τ_end - (τ_now + τ), τ)
         m_new = m
         happy = False

      else:

         # Local truncation error estimation
         err = abs(β * nrm * F[j-1, j])

         # Error for this step
         oldω = ω
         ω = τ_end * err / (τ * tol)

         # Estimate order
         if m == oldm and τ != oldτ and ireject >= 1:
            order = max(1, math.log(ω/oldω) / math.log(τ/oldτ))
            orderold = False
         elif orderold or ireject == 0:
            orderold = True
            order = j/4
         else:
            orderold = True

         # Estimate k
         if m != oldm and τ == oldτ and ireject >= 1:
            kest = max(1.1, (ω/oldω)**(1/(oldm-m)))
            kestold = False
         elif kestold or ireject == 0:
            kestold = True
            kest = 2
         else:
            kestold = True

         if ω > delta:
            remaining_time = τ_end - τ_now
         else:
            remaining_time = τ_end - (τ_now + τ)

         # Krylov adaptivity

         same_τ = min(remaining_time, τ)
         τ_opt  = τ * (γ / ω)**(1 / order)
         τ_opt  = min(remaining_time, max(τ/5, min(5*τ, τ_opt)))

         m_opt = math.ceil(j + math.log(ω / γ) / math.log(kest))
         m_opt = max(mmin, min(mmax, max(math.floor(3/4*m), min(m_opt, math.ceil(4/3*m)))))

         if j == mmax:
            if ω > delta:
               m_new = j
               τ_new = τ * (γ_mmax / ω)**(1 / order)
               τ_new = min(τ_end - τ_now, max(τ/5, τ_new))
            else:
               τ_new = τ_opt
               m_new = m
         else:
            m_new = m_opt
            τ_new = same_τ

      # Check error against target
      if ω <= delta:

         # Yep, got the required tolerance; update
         reject += ireject
         step   += 1

         # Udate for τ_out in the interval (τ_now, τ_now + τ)
         blownTs = 0
         nextT = τ_now + τ
         for k in range(l, numSteps):
            if abs(τ_out[k]) < abs(nextT):
               blownTs += 1

         if blownTs != 0:
            # Copy current w to w we continue with.
            w[:, l+blownTs] = w[:, l]

            for k in range(blownTs):
               τPhantom = τ_out[l+k] - τ_now
               F2 = scipy.linalg.expm(sgn * τPhantom * H[0:j, :j])
               w[:, l+k] = β * V[:n, :j] @ F2[:j, 0]

            # Advance l.
            l += blownTs

         # Using the standard scheme
         w[:, l] = β * V[:n, :j] @ F[:j, 0]

         # Update τ_out
         τ_now += τ

         j = 0
         ireject = 0

         conv += err

      else:
         # Nope, try again
         ireject += 1

         # Restore the original matrix
         H[0, j] = 0.0


      oldτ = τ
      τ    = τ_new

      oldm = m
      m    = m_new


   if task1:
      for k in range(numSteps):
         w[:, k] = w[:, k] / τ_out[k]

   m_ret=m

   stats = (step, reject, krystep, exps, conv, m_ret)

   return w, stats
