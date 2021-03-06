#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Mar  4 10:47:33 2021

@author: florianma
"""
from tqdm import trange  # Progress bar
import matplotlib.pyplot as plt
from finite_element_solver.domains.cylinder import CylinderMesh, CylinderDomain
from finite_element_solver.schemes.chorins_projection import (
    ImplicitTentativeVelocityStep, ExplicitTentativeVelocityStep, PressureStep,
    VelocityCorrectionStep)
from finite_element_solver.schemes.chorins_projection_tutorial import (
    TentativeVelocityStep, PressureStep, VelocityCorrectionStep)


def test():
    # all the IO and printing happens here
    my_parameters = {"density [kg/m3]": 1.0,
                     "viscosity [Pa*s]": 1e-3,
                     "characteristic length [m]": .1,
                     "velocity [m/s]": 1.5,
                     "dt [s]": 0.1
                     }
    my_mesh = CylinderMesh(lcar=0.02)
    my_domain = CylinderDomain(my_parameters["velocity [m/s]"], my_mesh.mesh)

    cfl = .05
    dt = cfl*my_domain.mesh.hmin()/my_domain.U_mean
    my_parameters["dt [s]"] = dt

    tvs = TentativeVelocityStep(my_parameters, my_domain)
    ps = PressureStep(my_parameters, my_domain)
    vcs = VelocityCorrectionStep(my_parameters, my_domain)

    my_mesh.plot()
    plt.show()

    rho = my_parameters["density [kg/m3]"]
    U = my_domain.U_mean
    L = my_parameters["characteristic length [m]"]
    mu = my_parameters["viscosity [Pa*s]"]
    Re = rho*U*L/mu
    print("Re = ", Re)
    print("rho = ", rho)
    print("mu = ", mu)
    print("dt = ", dt)

    for n in trange(8000):
        tvs.solve()
        ps.solve()
        vcs.solve()

        my_domain.u_1.assign(my_domain.u_)
        my_domain.p_1.assign(my_domain.p_)
        if (n % 100) == 0:
            fig, ax = my_domain.plot()
            plt.savefig("tst.png")
            plt.close()


if __name__ == "__main__":
    test()
