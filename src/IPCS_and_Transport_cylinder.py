#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Nov 11 10:29:50 2020

@author: florianma
"""
import numpy as np
import os
import matplotlib.pyplot as plt
import pygmsh
import timeit
import warnings
from tqdm import trange  # Progress bar
from dolfin import VectorElement, FiniteElement, Constant, inner, grad, div, \
    dx, Function, DirichletBC, Expression, solve, lhs, rhs, TestFunction, ds, \
    TrialFunction, dot, nabla_grad, split, errornorm, Mesh, plot, MeshEditor, \
    AutoSubDomain, MeshFunction, FacetNormal, assemble, Identity, \
    project, FunctionSpace, sym, Constant, UserExpression, VectorFunctionSpace


def plot_up(u, p, rho):
    velocity = u.compute_vertex_values(mesh)
    velocity.shape = (2, -1)
    magnitude = np.linalg.norm(velocity, axis=0)
    x, y = np.split(mesh.coordinates(), 2, 1)
    u, v = np.split(velocity, 2, 0)
    x, y, u, v = x.ravel(), y.ravel(), u.ravel(), v.ravel()
    tri = mesh.cells()
    pressure = p.compute_vertex_values(mesh)
    density = rho.compute_vertex_values(mesh)

    fig, (ax1, ax2, ax3) = plt.subplots(3, sharex=True, sharey=True,
                                        figsize=(12, 9))
    ax1.quiver(x, y, u, v, magnitude)
    ax2.tricontourf(x, y, tri, pressure, levels=40)
    ax3.tricontourf(x, y, tri, density, levels=40)
    ax1.set_aspect("equal")
    ax2.set_aspect("equal")
    ax3.set_aspect("equal")
    ax1.set_title("velocity")
    ax2.set_title("pressure")
    ax3.set_title("density")
    return fig, (ax1, ax2)


def time_stepping(mesh, VQL, bcs, ds_, N, dt, mu, rho, D):
    my_dir = "../mu({:.4f})_rho({:.4f})_D({:.4f})/".format(mu, rho, D)
    if not os.path.exists(my_dir):
        os.makedirs(my_dir)
    # D = 0.1  # Diffusion coefficient
    V, Q, L = VQL
    vu, vp, vr = TestFunction(V), TestFunction(Q), TestFunction(L)  # for integration
    u_, p_, r_ = Function(V), Function(Q), Function(L)  # for the solution
    u_1, p_1, r_1 = Function(V), Function(Q), Function(L)  # for the prev. solution
    u, p, r = TrialFunction(V), TrialFunction(Q), TrialFunction(L)  # unknown!

    bcu = [bcs[0], bcs[1]]  # note: no-slip at cylinder wall is no longer used!
    bcp = [bcs[2]]

    # Define symmetric gradient
    def epsilon(u):
        return sym(nabla_grad(u))

    # Define stress tensor
    def sigma(u, p):
        return 2*mu*epsilon(u) - p*Identity(len(u))

    class _rho_(UserExpression):
        def eval(self, values, x):
            if (x[0]-.2)*(x[0]-.2) + (x[1]-.2)*(x[1]-.2) < 0.0025:
                values[0] = rho*10.
            else:
                values[0] = rho
    f0 = _rho_(degree=2, element=L.ufl_element())
    r_1.assign(project(f0, mesh=mesh))

    n = FacetNormal(mesh)
    u_mid = (u + u_1) / 2.0
    F1 = r_1*dot((u - u_1) / dt, vu)*dx \
        + r_1*dot(dot(u_1, nabla_grad(u_1)), vu)*dx \
        + inner(sigma(u_mid, p_1), epsilon(vu))*dx \
        + dot(p_1*n, vu)*ds - dot(mu*nabla_grad(u_mid)*n, vu)*ds
    a1 = lhs(F1)
    L1 = rhs(F1)
    # Define variational problem for step 2
    a2 = dot(nabla_grad(p), nabla_grad(vp))*dx
    L2 = dot(nabla_grad(p_1), nabla_grad(vp))*dx - (1/dt)*div(u_)*vp*dx
    # Define variational problem for step 3
    a3 = dot(u, vu)*dx
    L3 = dot(u_, vu)*dx - dt*dot(nabla_grad(p_ - p_1), vu)*dx
    # Step 4: Transport of rho / Convection-diffusion and SUPG
    beta = 0.0
    r_mid = (r + r_1) / 2.0
    F4 = dot((r - r_1) / dt, vr) * dx \
        + dot(dot(u_, grad(r_mid)),  vr) * dx \
        + dot(D*grad(r_mid), grad(vr)) * dx
    # F4 += beta * inner(dot(u_, grad(r_mid), dot(u_, grad(vr)))) * dx  # SUPG
    a4 = lhs(F4)
    L4 = rhs(F4)
    # Assemble matrices
    A1 = assemble(a1)
    A2 = assemble(a2)
    A3 = assemble(a3)
    A4 = assemble(a4)
    # Apply boundary conditions to matrices
    [bc.apply(A1) for bc in bcu]
    [bc.apply(A2) for bc in bcp]

    u_x = np.zeros((N//2, len(p_.compute_vertex_values(mesh))),
                   dtype=np.float32)
    u_y = np.zeros_like(u_x)
    pressure = np.zeros_like(u_x)
    for n in trange(N):
        t = n*dt
        # Step 1: Tentative velocity step
        A1 = assemble(a1)   # needs to be reassembled because density changed!
        [bc.apply(A1) for bc in bcu]
        b1 = assemble(L1)
        [bc.apply(b1) for bc in bcu]
        solve(A1, u_.vector(), b1, 'bicgstab', 'hypre_amg')
        # Step 2: Pressure correction step
        b2 = assemble(L2)
        [bc.apply(b2) for bc in bcp]
        solve(A2, p_.vector(), b2, 'bicgstab', 'hypre_amg')
        # Step 3: Velocity correction step
        b3 = assemble(L3)
        solve(A3, u_.vector(), b3, 'cg', 'sor')
        # Step 4: Transport of rho / Convection-diffusion and SUPG
        A4 = assemble(a4)
        b4 = assemble(L4)
        solve(A4, r_.vector(), b4, 'bicgstab', 'hypre_amg')
        # F = (div(D*grad(rho_1)) - div(u_*rho_1))*dt + rho_1
        # rho_ = project(F, mesh=mesh)

        # Update previous solution
        u_1.assign(u_)
        p_1.assign(p_)
        r_1.assign(r_)

        if (n % 2) == 0:  # save every other snapshot
            i = n//2
            u_x[i], u_y[i] = np.split(u_.compute_vertex_values(mesh), 2, 0)
            pressure[i] = p_.compute_vertex_values(mesh)
            if ((n % 100) < 1e-4):
                fig, axs = plot_up(u_, p_, r_)
                plt.suptitle("t={:.2f} s".format(t))
                fn = my_dir+"frame_{:06.0f}.png".format(n+1)
                plt.savefig(fn)
                plt.close(fig)
        # if n > 630:
        #     fig, axs = plot_up(u_, p_, rho)
        #     plt.title("t={:.2f} s\n ({} solver)".format(t, solver))
        #     fn = "../cyl_transport_res/{:06.0f}.png".format(n+1)
        #     plt.savefig(fn)
        #     plt.close(fig)
    x, y = np.split(mesh.coordinates(), 2, 1)
    tri = mesh.cells()
    np.save(my_dir+"{:06.0f}x.npy".format(0), x.ravel())
    np.save(my_dir+"{:06.0f}y.npy".format(0), y.ravel())
    np.save(my_dir+"{:06.0f}t.npy".format(0), tri)
    np.save(my_dir+"{:06.0f}u.npy".format(n+1), u_x.ravel())
    np.save(my_dir+"{:06.0f}v.npy".format(n+1), u_y.ravel())
    np.save(my_dir+"{:06.0f}p.npy".format(n+1), pressure.ravel())
    return


def create2Dmesh(msh):
    """
    Helping function to create a 2D mesh for FEniCS from a gmsh.
    important! Dont leave any unused points like the center of the circle in
    the node list. FEniCS will crash!
    """
    msh.prune_z_0()
    nodes = msh.points[1:]
    cells = msh.cells_dict["triangle"].astype(np.uintp)-1
    mesh = Mesh()
    editor = MeshEditor()
    # point, interval, triangle, quadrilateral, hexahedron
    editor.open(mesh, "triangle", 2, 2)
    editor.init_vertices(len(nodes))
    editor.init_cells(len(cells))
    [editor.add_vertex(i, n) for i, n in enumerate(nodes)]
    [editor.add_cell(i, n) for i, n in enumerate(cells)]
    editor.close()
    return mesh


def cylinder(lcar):
    with pygmsh.geo.Geometry() as geom:
        r = .05
        p = [geom.add_point([.20, .20], lcar),
             geom.add_point([0.0, .0], lcar),
             geom.add_point([2.2, .0], lcar),
             geom.add_point([2.2, .41], lcar),
             geom.add_point([0.0, .41], lcar),
             geom.add_point([.2+r, .20], lcar),
             geom.add_point([.20, .2+r], lcar),
             geom.add_point([.2-r, .20], lcar),
             geom.add_point([.20, .2-r], lcar)]
        c = [geom.add_line(p[1], p[2]),
             geom.add_line(p[2], p[3]),
             geom.add_line(p[3], p[4]),
             geom.add_line(p[4], p[1]),
             geom.add_circle_arc(p[5], p[0], p[6]),
             geom.add_circle_arc(p[6], p[0], p[7]),
             geom.add_circle_arc(p[7], p[0], p[8]),
             geom.add_circle_arc(p[8], p[0], p[5])]
        ll1 = geom.add_curve_loop([c[0], c[1], c[2], c[3]])
        ll2 = geom.add_curve_loop([c[4], c[5], c[6], c[7]])
        s = [geom.add_plane_surface(ll1, [ll2])]
        # s = [geom.add_plane_surface(ll1)]
        geom.add_surface_loop(s)
        msh = geom.generate_mesh()
    mesh = create2Dmesh(msh)
    return mesh


def rectangle(lcar):
    with pygmsh.geo.Geometry() as geom:
        p = [geom.add_point([.20, .20], lcar),
             geom.add_point([0.0, .0], lcar),
             geom.add_point([2.2, .0], lcar),
             geom.add_point([2.2, .41], lcar),
             geom.add_point([0.0, .41], lcar)]
        c = [geom.add_line(p[1], p[2]),
             geom.add_line(p[2], p[3]),
             geom.add_line(p[3], p[4]),
             geom.add_line(p[4], p[1])]
        ll1 = geom.add_curve_loop([c[0], c[1], c[2], c[3]])
        s = [geom.add_plane_surface(ll1)]
        geom.add_surface_loop(s)
        msh = geom.generate_mesh()
    mesh = create2Dmesh(msh)
    return mesh


def stokes(vup, up, up_, bcs, mu):
    u, p = split(up)  # Trial
    vu, vp = split(vup)  # Test
    u_, p_ = split(up_)  # Function holding the solution
    F = mu*inner(grad(vu), grad(u))*dx - inner(div(vu), p)*dx \
        - inner(vp, div(u))*dx
    solve(lhs(F) == rhs(F), up_, bcs=bcs)
    return up_


def topandbottom(x, on_boundary):
    return (x[1] < 1e-6) or (.4099 < x[1]) and on_boundary


def cylinderwall(x, on_boundary):
    in_circle = ((x[0]-.2)*(x[0]-.2) + (x[1]-.2)*(x[1]-.2)) < 0.0025001
    return (in_circle)  # & on_boundary


def inlet(x, on_boundary):
    return (x[0] < 1e-6) and on_boundary


def outlet(x, on_boundary):
    return (abs(x[0]-2.2) < 1e-6) and on_boundary


def setup_cylinder_problem(mesh, U0, coupled=True):
    # Build function space
    V = VectorFunctionSpace(mesh, 'P', 2)
    Q = FunctionSpace(mesh, 'P', 1)
    L = FunctionSpace(mesh, 'P', 1)
    VQ = (V, Q, L)

    # bc0 = DirichletBC(V, Constant((0, 0)), cylinderwall)
    bc1 = DirichletBC(V, Constant((0, 0)), topandbottom)
    bc2 = DirichletBC(V, U0, inlet)
    bc3 = DirichletBC(Q, Constant(1), outlet)
    # bcs = [bc0, bc1, bc2, bc3]
    bcs = [bc1, bc2, bc3]
    warnings.warn("no no-slip for cyl-wall!")

    ASD1 = AutoSubDomain(topandbottom)
    ASD2 = AutoSubDomain(cylinderwall)
    mf = MeshFunction("size_t", mesh, 1)
    mf.set_all(0)
    ASD1.mark(mf, 1)
    ASD2.mark(mf, 2)
    ds_ = ds(subdomain_data=mf, domain=mesh)
    return VQ, bcs, ds_


if __name__ == "__main__":
    cfl = .01
    T = 8
    # nu = 1e-3
    mu = 1e-3 * 1
    rho = 1.
    D = .01
    # U_m = .3
    U_m = 1.5
    U0_str = "4.*U_m*x[1]*(.41-x[1])/(.41*.41)"
    mesh = rectangle(.01)
    U0 = Expression((U0_str, "0"), U_m=U_m, degree=2)
    # mu = nu*rho
    nu = mu/rho
    x = [0, .41/2]  # evaluate the Expression at the center of the channel
    U_mean = 2/3*eval(U0_str)
    dt = cfl*mesh.hmin()/np.mean(U_mean)
    N = int((T/dt) // 1)
    L = .1

    print(dt)
    print("Re set to: ", rho*U_mean*L/mu)
    print("Re set to: ", U_mean*L/nu)
    print("cfl number: ", np.mean(U_mean)*dt/mesh.hmin())
    print(N, "timesteps")
    print("Unknowns: ", mesh.num_edges())
    print("coordinates: ", len(mesh.coordinates()))

    VQ, bcs, ds_ = setup_cylinder_problem(mesh, U0, coupled=False)
    tic = timeit.default_timer()
    time_stepping(mesh, VQ, bcs, ds_, N, dt, mu, rho, D)
    toc = timeit.default_timer()

    print("Re set to: ", U_mean*.1/mu)
    print("cfl number: ", np.mean(U_mean)*dt/mesh.hmin())
    print(N, "timesteps")
    print("Unknowns: ", mesh.num_edges())
    print("coordinates: ", len(mesh.coordinates()))
    print("time IPCS:", toc-tic)
