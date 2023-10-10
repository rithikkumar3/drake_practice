import numpy as np
from pydrake.all import *
import time
from simple_block_world import *
# from quat_operations import *

# ======================
# WORLD
# ======================

dt = 0.01
builder = DiagramBuilder()
plant, scene_graph = AddMultibodyPlantSceneGraph(builder, dt)

add_ground(plant)
color = np.array([0.0, 0.0, 1.0, 0.3])
cube_name = "cube1"
edge_length = 0.1
mass = 1.0
add_cube(plant, cube_name, color, edge_length, mass)

plant.mutable_gravity_field().set_gravity_vector(np.zeros(3))


plant.Finalize()
print_plant_info(plant, scene_graph)

# Connect to visualizer
params = DrakeVisualizerParams(role=Role.kProximity, show_hydroelastic=True)
DrakeVisualizer(params=params).AddToBuilder(builder, scene_graph)
# ConnectContactResultsToDrakeVisualizer(builder, plant, scene_graph)

diagram = builder.Build()

diagram_context = diagram.CreateDefaultContext()
plant_context = plant.GetMyContextFromRoot(diagram_context)

# Create a AutoDiffXd correspondence
diagram_ad = diagram.ToAutoDiffXd()
diagram_context_ad = diagram_ad.CreateDefaultContext()
plant_ad = diagram_ad.GetSubsystemByName(plant.get_name())
plant_context_ad = plant_ad.GetMyContextFromRoot(diagram_context_ad)

query_port = plant.get_geometry_query_input_port()
query_object = query_port.Eval(plant_context)
inspector = query_object.inspector()

# Create query port for AutoDiff
query_port_ad = plant_ad.get_geometry_query_input_port()
all_contact_pairs = inspector.GetCollisionCandidates()


# ======================
# DESIRED STATES
# ======================

# Set the initial state
q0_cube1 = np.array([1., 0., 0., 0., 0.0, 0., 0.05])
v0 = np.zeros(plant.num_velocities())
x0 = np.hstack((q0_cube1, v0))   # horizontal stacking one after the other

# desired final state
qf_cube1 = np.array([1., 0., 0., 0., 0.5, 0.0, 0.05])
xf = np.hstack((qf_cube1, v0))

diagram_context.SetDiscreteState(x0)
diagram.ForcedPublish(diagram_context)

simulator = Simulator(diagram, diagram_context)
simulator.set_publish_every_time_step(False)
simulator.set_target_realtime_rate(1.0)
simulator.Initialize()
simulator.AdvanceTo(0.001)


# ======================
# OPTIMIZATION VARIABLES
# ======================

prog = MathematicalProgram()
N = 10
print("N: ", N)
nq = plant.num_positions()
print("nq: ", nq)
nv = plant.num_velocities()
print("nv: ", nv)
n = nq + nv
print("n: ", n)
m = plant.num_velocities()

# Weights on penalizing the relaxation and the joint effort
w_relax = 1e3
w_effort = 1.0

# control limits [N]
tau_max = 1000.0

max_force = np.array([tau_max, tau_max, tau_max])
min_force = np.array([-1., -1., -1.])

# Initialize the decision variables
# States
x = np.empty((n, N+1), dtype=Variable)
# Controls
# u = np.empty((m, N), dtype=Variable)

impulse_force = np.empty((3, N), dtype=Variable)  # <-- New Variable

# for i in range(N):
# prog.SetInitialGuess(impulse_force[:, i], np.array([0.1, 0.1, 0.1]))  # Setting a non-zero initial guess

# Add continuous variables to the program for each parameter
for i in range(N):
    x[:, i] = prog.NewContinuousVariables(n, 'x' + str(i))
    # u[:, i] = prog.NewContinuousVariables(m, 'u' + str(i))
    impulse_force[:, i] = prog.NewContinuousVariables(3, 'impulse' + str(i))
x[:, N] = prog.NewContinuousVariables(n, 'x' + str(N))

# for i in range(N):
#     for j in range(3):
#         prog.SetInitialGuess(impulse_force[j, i], 0.1)

# Initial state
cnstr_x0 = prog.AddBoundingBoxConstraint(x0, x0, x[:, 0]).evaluator()
cnstr_x0.set_description("initial sphere/box state")

# cnstr_impulse = prog.AddBoundingBoxConstraint(
#     min_force, max_force, impulse_force[0:3, 0]).evaluator()
# Final
cnstr_vf = prog.AddBoundingBoxConstraint(xf, xf, x[:, N]).evaluator()
cnstr_vf.set_description("final sphere pose")


# cnstr_impulse = prog.AddBoundingBoxConstraint(
#     np.zeros((3, N-1)), np.zeros((3, N-1)), impulse_force[0:3, 1:N]).evaluator()
# cnstr_vf.set_description("final sphere pose")

xx1_idx = q0_cube1.size-3
xy1_idx = q0_cube1.size-2
xz1_idx = q0_cube1.size-1
print("xx1_idx: ", xx1_idx)

vx1_idx = nq + nv-9
vy1_idx = nq + nv-8
vz1_idx = nq + nv-7
print("vx1_idx: ", vx1_idx)


# ======================
# OPTIMIZATION PROGRAM
# ======================


def eval_vel_constraints(z):
    # Select the data type according to the input
    eval_plant = plant_ad
    eval_context = plant_context_ad
    eval_port = query_port_ad
    eval_type = AutoDiffXd

    if z.dtype == float:
        eval_plant = plant
        eval_context = plant_context
        eval_port = query_port
        eval_type = float

    q_curr = z[:nq]
    q_next = z[n:n+nq]
    v_curr = z[nq:n]
    v_next = z[n+nq:2*n]
    impulse = z[2*n:2*n+3]
    print("v_curr\n", ExtractValue(v_curr).transpose())
    print("v_next\n", ExtractValue(v_next).transpose())
    print("impulse\n", ExtractValue(impulse).transpose())
    print("impulse/mass*dt\n", ExtractValue(impulse/mass*dt).transpose())

    # v_next[3:6] = v_curr[3:6] + impulse/mass*dt
    # print("v_curr[3:6]\n", ExtractValue(v_curr[3:6]).transpose())
    v1 = v_next[3:6] - (v_curr[3:6] + impulse/mass*dt)

    pos_inc = v_curr[3:6]*dt
    print("pos_inc\n", ExtractValue(pos_inc).transpose())

    # ori_inc = v_curr[0:3]*dt*edge_length
    pos1 = q_curr[4:7] + pos_inc - q_next[4:7]
    print("pos1\n", ExtractValue(pos1).transpose())

    return np.hstack((pos1, v1))


# Build the program
for i in range(N+1):

    prog.SetInitialGuess(x[:, i], x0)

    AddUnitQuaternionConstraintOnPlant(
        plant, x[:plant.num_positions(), i], prog)

    AddUnitQuaternionConstraintOnPlant(
        plant_ad, x[:plant_ad.num_positions(), i], prog)

    prog.AddBoundingBoxConstraint(
        np.zeros(3), np.zeros(3), x[7:10, i])

    if i < N:
        prog.SetInitialGuess(
            impulse_force[:, i], np.array([1.0, 1.0, 1.0]))

        v_vars = np.hstack((x[:, i], x[:, i+1], impulse_force[:, i]))

        cnstr_vel = prog.AddConstraint(
            eval_vel_constraints,
            lb=np.hstack((np.zeros(3+3))),
            ub=np.hstack((np.zeros(3+3))),
            vars=v_vars).evaluator()
        cnstr_vel.set_description(f"cnstr_vel at step {i}")

        cnstr_impulse = prog.AddBoundingBoxConstraint(
            0.0*np.ones(3), max_force, impulse_force[:, i]).evaluator()

        # cnstr_tau_pos = prog.AddBoundingBoxConstraint(
        #     -tau_max * np.ones(3), tau_max * np.ones(3), u[0:3, i]).evaluator()
        # cnstr_tau_pos.set_description(f"cnstr_tau_pos at step {i}")

        # cnstr_tau_ori = prog.AddBoundingBoxConstraint(
        #     -tau_max * np.ones(3), tau_max * np.ones(3), u[3:6, i]).evaluator()
        # cnstr_tau_ori.set_description(f"cnstr_tau_ori at step {i}")

        # Penalize the slack variable
        # prog.AddLinearCost(w_effort*np.sum(u[:, i]))
        # prog.AddCost(np.sum(impulse_force[:, i]**2))


# ======================
# SOLVE
# ======================

# Select solver
# solver = IpoptSolver()
solver = SnoptSolver()
# Set solver options
solver_options = SolverOptions()
solver_options.SetOption(CommonSolverOption.kPrintToConsole, 1)
solver_options.SetOption(CommonSolverOption.kPrintFileName, "solver.txt")
if solver.solver_type() == SolverType.kIpopt:
    solver_options.SetOption(solver.id(), "print_level", 5)
# Solve the program
t_start = time.time()
result = solver.Solve(prog, solver_options=solver_options)
print(f"Optimization took {time.time() - t_start} s")
print(f"Success: {result.is_success()}")
if solver.solver_type() == SolverType.kSnopt:
    print(f"Exit condition: {result.get_solver_details().info}")

# Report constraint violations
infeasible_constraints = result.GetInfeasibleConstraints(prog)
for c in infeasible_constraints:
    print(f"Violated constraint: {c}")
    print(f"\tViolation: {result.EvalBinding(c)}\n")

# Get and print the solution
x_sol = result.GetSolution(x)
# u_sol = result.GetSolution(u)
print("x_sol\n", x_sol.transpose())

# <-- Fetch the impulsive force from the result
impulsive_force_sol = result.GetSolution(impulse_force)
print(f"Impulsive Force\n: {impulsive_force_sol.transpose()}")

infeasible_constraints = result.GetInfeasibleConstraints(prog)
for c in infeasible_constraints:
    print(f"Violated constraint: {c}")
    print(f"\tViolation: {result.EvalBinding(c)}\n")

# ======================
# SIMULATE
# ======================

simulate = True
if simulate:
    while True:
        tm = 0
        # Just keep playing back the trajectory
        for i in range(N+1):
            xs = x_sol[:, i]
            # print(xs)

            diagram_context.SetTime(tm)
            plant.SetPositionsAndVelocities(plant_context, xs)
            diagram.ForcedPublish(diagram_context)

            tm += dt

            time.sleep(1)
        time.sleep(1)
