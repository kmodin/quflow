import numpy as np
import scipy.linalg
import inspect

from ..laplacian import solve_poisson, laplace
from ..geometry import norm_Linf, hbar
from .isospectral import commutator, conj_subtract_
from .isospectral import _SKEW_HERM_

def solve_mhd(state):
    """
    Hamiltonian function for the standard MHD system.
    """
    W = state[0,:,:]
    Theta = state[1,:,:]
    P = solve_poisson(W)
    B = laplace(Theta)
    return P, B


def magmp_fixedpoint(state, 
                     stepsize, 
                     steps=100, 
                     hamiltonian=solve_mhd, 
                     time=None, 
                     forcing=None, 
                     stats=None,
                     callback=None,
                     tol='auto', 
                     maxit=10, 
                     minit=1, 
                     verbatim=False, 
                     reinitialize=False
                     ):
    """
    Time-stepping by magnetic midpoint second order method for skew-Hermitian W
    using fixed-point iterations. The equations are
        W' = [P, W] + [B, Theta]
        Theta' = [P, Theta]

    Parameters
    ----------
    state: ndarray, shape = (2,N,N)
        Initial state given as the skew-Hermitian vorticity 
        matrices W = state[0,:,:] and Theta = state[1,:,:].
    stepsize: float
        Time step length.
    steps: int
        Number of steps to take.
    hamiltonian: function(state) or function(state, time)
        The Hamiltonian returning (P, B).
    time: float or None (default)
        Time at the initial state. If `None` the system is assumed to
        be autonomous, and the time parameter is not passed to the hamiltonian and forcing.
    forcing: function(P, W) or function(P, W, time) or None (default)
        Extra force function (to allow non-isospectral perturbations).
    stats: dict or None
        Dictionary to be filled in with integration statistics.
    callback: function(W, dW, stats) or None (default)
        Callback function evaluated at the end of every step, 
        just before W is updated W += dW.
    tol: float or 'auto' (default)
        Tolerance for iterations. Negative value or "auto" means automatic choice.
    maxit: int
        Maximum number of iterations.
    minit: int
        Minimum number of iterations.
    verbatim: bool
        Print extra information if True. Default is False.
    compsum: bool
        Use compensated summation.
    reinitialize: bool
        Whether to re-initiate the iteration vector at every step.

    Returns
    -------
    W: ndarray
    """

    # Check input
    assert minit >= 1, "minit must be at least 1."
    assert maxit >= minit, "maxit must be at minit."
    assert _SKEW_HERM_, "MAGMP only works for skew-Hermitian matrices."

    # Check if force function is autonomous
    if forcing is not None:
        autonomous_force = True
        if time is not None and 'time' in inspect.getfullargspec(forcing).args:
            autonomous_force = False
            FW = np.zeros_like(state)
    
    # Check if autonomous
    autonomous = True
    if time is not None and 'time' in inspect.getfullargspec(hamiltonian).args:
        autonomous = False

    # Stats variables
    total_iterations = 0
    number_of_maxit = 0

    # Initialize
    dstate = np.zeros_like(state)
    dstate_old = np.zeros_like(state)
    statehalf = np.zeros_like(state)
    Pstatecomm = np.zeros_like(state)
    BThetacomm = np.zeros_like(state[0,:,:])
    BThetaPhalf = np.zeros_like(BThetacomm)
    # PWPhalf = np.zeros_like(W)
    hhalf = stepsize/2.0
    hb = hbar(N=state.shape[-1])

    # Specify tolerance if needed
    if tol == "auto" or tol < 0:
        mach_eps = np.finfo(state.dtype).eps
        if state.ndim > 2:
            zeroind = (0,)*(state.ndim-2) + (Ellipsis,)
            tol = np.sqrt(mach_eps)*stepsize*np.linalg.norm(state[zeroind], np.inf)
        else:
            tol = np.sqrt(mach_eps)*stepsize*np.linalg.norm(state, np.inf)
        if verbatim:
            print("Tolerance set to {}.".format(tol))
        if stats:
            stats['tol'] = tol

    # --- Beginning of step loop ---
    for k in range(steps):

        # Per step updates
        resnorm = np.inf
        if reinitialize:
            dstate.fill(0.0)

        # --- Beginning of iterations ---
        for i in range(maxit):

            # Update iterations
            total_iterations += 1

            # Compute Wtilde
            np.copyto(statehalf, state)
            statehalf += dstate
            Thetahalf = statehalf[1,:,:]

            # Save dW from previous step
            np.copyto(dstate_old, dstate)

            # Update Ptilde
            if autonomous:
                Phalf, Bhalf = hamiltonian(statehalf)
            else:
                Phalf, Bhalf = hamiltonian(statehalf, time=time + hhalf)
            Phalf *= hhalf
            Bhalf *= hhalf

            # Compute middle variables
            # PWcomm = Phalf @ Whalf  # old
            np.matmul(Phalf, statehalf, out=Pstatecomm)
            np.matmul(Bhalf, Thetahalf, out=BThetacomm)

            # PWPhalf = PWcomm @ Phalf  # old
            # np.matmul(PWcomm, Phalf, out=PWPhalf)
            np.matmul(Pstatecomm, Phalf, out=dstate)  # More efficient, as PWPhalf is not needed
            conj_subtract_(Pstatecomm, Pstatecomm)
            np.matmul(BThetacomm, Phalf, out=BThetaPhalf)
            conj_subtract_(BThetacomm, BThetacomm)

            # Update dW
            dstate += Pstatecomm
            dstate[0,:,:] += BThetaPhalf
            dstate[0,:,:] -= BThetaPhalf.T.conj()
            dstate[0,:,:] += BThetacomm

            # Add forcing if needed
            if forcing is not None:
                if autonomous_force:
                    FW = forcing(Phalf/hhalf, statehalf)
                else:
                    FW = forcing(Phalf/hhalf, statehalf, time=time + hhalf)
                FW *= hhalf*hb
                dstate += FW

            # Check if time to break
            if i+1 >= minit:
                # Compute error
                resnorm_old = resnorm
                dstate_old -= dstate
                if dstate_old.ndim > 2:
                    resnormvec = scipy.linalg.norm(dstate_old, ord=np.inf, axis=(-1, -2))
                    if Phalf.ndim == 2:
                        resnorm = resnormvec[0]
                    else:
                        resnorm = resnormvec.max()
                else:
                    resnorm = scipy.linalg.norm(dstate_old, ord=np.inf)
                if resnorm <= tol or resnorm >= resnorm_old:
                    break

        else:
            # We used maxit iterations
            number_of_maxit += 1
            if verbatim:
                print("Max iterations {} reached at step {}.".format(maxit, k))
            # if stats:
            #     stats['maxit_reached'] = True

        # Rescale commutators (as they are divided by 2)
        Pstatecomm *= 2
        BThetacomm *= 2

        # Evaluate callback function
        if callback is not None:
            callback(state, Pstatecomm)

        # Update state
        state += Pstatecomm
        state[0,:,:] += BThetacomm   
        if forcing is not None:
            FW *= 2
            state += FW

        if time:
            time += stepsize

        # --- End of step ---

    if verbatim:
        print("Average number of iterations per step: {:.2f}".format(total_iterations/steps))
    if stats:
        stats["iterations"] = total_iterations/steps
        stats["maxit"] = number_of_maxit/steps

    return state


# Default magnetic integrator
magmp = magmp_fixedpoint
