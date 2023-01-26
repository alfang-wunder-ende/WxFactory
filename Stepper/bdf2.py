import numpy
from time import time

from Output.solver_stats import write_solver_stats
from Solver.nonlin       import newton_krylov
from Stepper.stepper     import Stepper

class bdf2(Stepper):
   def __init__(self, rhs, tol, preconditioner=None, init_substeps=1):
      super().__init__(preconditioner)
      self.rhs = rhs
      self.tol = tol
      self.init_substeps = init_substeps
      self.Qprev = None

   def __step__(self, Q, dt):
      t0 = time()
      if self.Qprev is None:
         # Initialize with the backward Euler method
         newQ = Q.copy()
         for _ in range(self.init_substeps):
            init_dt = dt / self.init_substeps
            nonlin_fun = lambda Q_plus: (Q_plus - newQ) / init_dt - 0.5 * self.rhs(Q_plus)

            newQ, nb_iter, residuals = newton_krylov(nonlin_fun, newQ, f_tol=self.tol)
      else:
         maxiter = None
         def nonlin_fun(Q_plus): return (Q_plus - 4./3. * Q + 1./3. * self.Qprev) / dt - 2./3. * self.rhs(Q_plus)
         if self.preconditioner is not None:
            self.preconditioner.prepare(dt, Q, self.Qprev)
            maxiter = 800
         newQ, nb_iter, residuals = newton_krylov(nonlin_fun, Q, f_tol=self.tol, fgmres_precond=self.preconditioner, verbose=False, maxiter=maxiter)
      t1 = time()

      write_solver_stats(nb_iter, t1 - t0, 0, residuals)

      self.Qprev = Q.copy()

      return numpy.reshape(newQ, Q.shape)
