import matplotlib.pyplot as plt
import ctypes
import ast
import os
from typing import List

from attr import attrs, attrib, Factory
from fmpy import read_model_description, extract

from fmpy.fmi1 import printLogMessage
from fmpy.fmi2 import FMU2Model, fmi2CallbackFunctions, fmi2CallbackLoggerTYPE, fmi2CallbackAllocateMemoryTYPE, \
    fmi2CallbackFreeMemoryTYPE, allocateMemory, freeMemory, fmi2EventInfo
from fmpy.simulation import apply_start_values, Input
from fmpy.util import auto_interval

from numerous import EquationBase, Equation, NumerousFunction
from numerous.engine.model import Model
from numerous.engine.simulation import Simulation, SolverType
from numerous.engine.system import Subsystem, Item, ItemPath
from numba import cfunc, carray, types, njit
import numpy as np

from numerous.engine.system.fmu_system_generator.fmu_ast_generator import generate_fmu_eval, generate_eval_llvm, \
    generate_eval_event, generate_njit_event_cond, generate_action_event, generate_event_action
from numerous.engine.system.fmu_system_generator.utils import address_as_void_pointer


class FMU_Subsystem(Subsystem, EquationBase):
    """
    """

    def __init__(self, fmu_filename: str, tag: str):
        super().__init__(tag)
        self.model_description = None
        input = None
        fmi_call_logger = None
        start_values = {}
        validate = False
        step_size = None
        output_interval = None
        start_values = start_values
        apply_default_start_values = False
        input = input
        debug_logging = False
        visible = False
        model_description = read_model_description(fmu_filename, validate=validate)
        self.model_description = model_description
        self.set_variables(self.model_description)
        required_paths = ['resources', 'binaries/']
        tempdir = extract(fmu_filename, include=lambda n: n.startswith(tuple(required_paths)))
        unzipdir = tempdir
        fmi_type = "ModelExchange"
        experiment = model_description.defaultExperiment
        start_time = 0.0
        start_time = float(start_time)

        stop_time = start_time + 1.0

        stop_time = float(stop_time)

        if step_size is None:
            total_time = stop_time - start_time
            step_size = 10 ** (np.round(np.log10(total_time)) - 3)

        if output_interval is None and fmi_type == 'CoSimulation' and experiment is not None and experiment.stepSize is not None:
            output_interval = experiment.stepSize
            while (stop_time - start_time) / output_interval > 1000:
                output_interval *= 2

        fmu_args = {
            'guid': model_description.guid,
            'unzipDirectory': unzipdir,
            'instanceName': None,
            'fmiCallLogger': fmi_call_logger
        }
        logger = printLogMessage
        callbacks = fmi2CallbackFunctions()
        callbacks.logger = fmi2CallbackLoggerTYPE(logger)
        callbacks.allocateMemory = fmi2CallbackAllocateMemoryTYPE(allocateMemory)
        callbacks.freeMemory = fmi2CallbackFreeMemoryTYPE(freeMemory)

        fmu_args['modelIdentifier'] = model_description.modelExchange.modelIdentifier

        fmu = FMU2Model(**fmu_args)
        self.fmu = fmu
        fmu.instantiate(visible=visible, callbacks=callbacks, loggingOn=debug_logging)

        if output_interval is None:
            if step_size is None:
                output_interval = auto_interval(stop_time - start_time)
            else:
                output_interval = step_size
                while (stop_time - start_time) / output_interval > 1000:
                    output_interval *= 2

        if step_size is None:
            step_size = output_interval
            max_step = (stop_time - start_time) / 1000
            while step_size > max_step:
                step_size /= 2

        fmu.setupExperiment(startTime=start_time, stopTime=stop_time)

        input = Input(fmu, model_description, input)

        apply_start_values(fmu, model_description, start_values, apply_default_start_values)

        fmu.enterInitializationMode()
        input.apply(start_time)
        fmu.exitInitializationMode()

        getreal = getattr(fmu.dll, "fmi2GetReal")
        component = fmu.component

        getreal.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
        getreal.restype = ctypes.c_uint

        set_time = getattr(fmu.dll, "fmi2SetTime")
        set_time.argtypes = [ctypes.c_void_p, ctypes.c_double]
        set_time.restype = ctypes.c_int

        fmi2SetReal = getattr(fmu.dll, "fmi2SetReal")
        fmi2SetReal.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
        fmi2SetReal.restype = ctypes.c_uint

        completedIntegratorStep = getattr(fmu.dll, "fmi2CompletedIntegratorStep")
        completedIntegratorStep.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                            ctypes.c_void_p]
        completedIntegratorStep.restype = ctypes.c_uint

        get_event_indicators = getattr(fmu.dll, "fmi2GetEventIndicators")

        get_event_indicators.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        get_event_indicators.restype = ctypes.c_uint

        enter_event_mode = getattr(fmu.dll, "fmi2EnterEventMode")

        enter_event_mode.argtypes = [ctypes.c_uint]
        enter_event_mode.restype = ctypes.c_uint

        enter_cont_mode = getattr(fmu.dll, "fmi2EnterContinuousTimeMode")
        enter_cont_mode.argtypes = [ctypes.c_uint]
        enter_cont_mode.restype = ctypes.c_uint

        newDiscreteStates = getattr(fmu.dll, "fmi2NewDiscreteStates")
        newDiscreteStates.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        newDiscreteStates.restype = ctypes.c_uint

        len_q = len(model_description.modelVariables)

        term_1 = np.array([0], dtype=np.int32)
        term_1_ptr = term_1.ctypes.data
        event_1 = np.array([0], dtype=np.int32)
        event_1_ptr = event_1.ctypes.data
        var_array = []
        ptr_var_array = []
        idx_tuple_array = []
        ptr_tuple_array = []
        for i in range(len_q):
            a = np.array([0], dtype=np.float64)
            var_array.append(a)
            ptr_var_array.append(a.ctypes.data)
            idx_tuple_array.append(("a_i_" + str(i), 'a' + str(i)))
            ptr_tuple_array.append(("a" + str(i) + "_ptr", 'a' + str(i)))

        fmu.enterContinuousTimeMode()

        q1, equation_call_wrapper = generate_eval_llvm(idx_tuple_array, [('a_i_1', 'a1'), ('a_i_3', 'a3')])
        module_func = ast.Module(body=[q1, equation_call_wrapper], type_ignores=[])
        code = compile(ast.parse(ast.unparse(module_func)), filename='fmu_eval', mode='exec')
        namespace = {"carray": carray, "cfunc": cfunc, "types": types, "np": np, "len_q": len_q, "getreal": getreal,
                     "component": component, "fmi2SetReal": fmi2SetReal, "set_time": set_time,
                     "completedIntegratorStep": completedIntegratorStep}
        exec(code, namespace)
        equation_call = namespace["equation_call"]

        q = generate_fmu_eval(['h', 'v', 'g', 'e'], ptr_tuple_array,
                              [('a1_ptr', 'a1'), ('a3_ptr', 'a3')])
        module_func = ast.Module(body=[q], type_ignores=[])
        code = compile(ast.parse(ast.unparse(module_func)), filename='fmu_eval', mode='exec')
        namespace = {"NumerousFunction": NumerousFunction, "carray": carray,
                     "address_as_void_pointer": address_as_void_pointer,
                     "equation_call": equation_call,
                     "event_1_ptr": event_1_ptr,
                     "term_1_ptr": term_1_ptr
                     }

        for i in range(len_q):
            namespace.update({"a" + str(i): var_array[i]})
            namespace.update({"a" + str(i) + "_ptr": ptr_var_array[i]})
        exec(code, namespace)
        self.fmu_eval = namespace["fmu_eval"]

        event_n = 1

        q, wrapper = generate_eval_event([0, 2], len_q)
        module_func = ast.Module(body=[q, wrapper], type_ignores=[])
        code = compile(ast.parse(ast.unparse(module_func)), filename='fmu_eval', mode='exec')
        namespace = {"carray": carray, "event_n": event_n, "cfunc": cfunc, "types": types, "np": np, "len_q": len_q,
                     "getreal": getreal,
                     "component": component, "fmi2SetReal": fmi2SetReal, "set_time": set_time,
                     "get_event_indicators": get_event_indicators,
                     "completedIntegratorStep": completedIntegratorStep}
        exec(code, namespace)
        event_ind_call_1 = namespace["event_ind_call_1"]

        c = np.array([0], dtype=np.float64)
        c_ptr = c.ctypes.data

        f1, f2 = generate_njit_event_cond(['t1.h', 't1.v'])
        module_func = ast.Module(body=[f1, f2], type_ignores=[])
        code = compile(ast.parse(ast.unparse(module_func)), filename='fmu_eval_2', mode='exec')
        namespace = {"carray": carray, "event_n": event_n, "cfunc": cfunc, "types": types, "np": np,
                     "event_ind_call_1": event_ind_call_1,
                     "c_ptr": c_ptr,
                     "component": component, "fmi2SetReal": fmi2SetReal, "set_time": set_time,
                     "njit": njit, "address_as_void_pointer": address_as_void_pointer,
                     "completedIntegratorStep": completedIntegratorStep}
        exec(code, namespace)
        event_cond = namespace["event_cond"]
        event_cond_2 = namespace["event_cond_2"]
        event_cond_2.lines = ast.unparse(ast.Module(body=[f2], type_ignores=[]))

        a, b = generate_action_event(len_q)
        module_func = ast.Module(body=[a, b], type_ignores=[])
        code = compile(ast.parse(ast.unparse(module_func)), filename='fmu_eval', mode='exec')
        namespace = {"carray": carray, "event_n": event_n, "cfunc": cfunc, "types": types, "np": np, "len_q": len_q,
                     "getreal": getreal,
                     "component": component, "enter_event_mode": enter_event_mode, "set_time": set_time,
                     "get_event_indicators": get_event_indicators, "newDiscreteStates": newDiscreteStates,
                     "enter_cont_mode": enter_cont_mode,
                     "completedIntegratorStep": completedIntegratorStep}
        exec(code, namespace)
        event_ind_call = namespace["event_ind_call"]

        event_info = fmi2EventInfo()
        q_ptr = ctypes.addressof(event_info)

        a_e_0 = np.array([0], dtype=np.float64)
        a_e_ptr_0 = a_e_0.ctypes.data

        a_e_1 = np.array([0], dtype=np.float64)
        a_e_ptr_1 = a_e_1.ctypes.data

        a_e_2 = np.array([0], dtype=np.float64)
        a_e_ptr_2 = a_e_2.ctypes.data

        a_e_3 = np.array([0], dtype=np.float64)
        a_e_ptr_3 = a_e_3.ctypes.data

        a_e_4 = np.array([0], dtype=np.float64)
        a_e_ptr_4 = a_e_4.ctypes.data

        a_e_5 = np.array([0], dtype=np.float64)
        a_e_ptr_5 = a_e_5.ctypes.data

        a1, b1 = generate_event_action(len_q, ['t1.h', 't1.v'],
                                       ['t1.h', 't1.h_dot', 't1.v', 't1.v_dot', 't1.g', 't1.e'])

        module_func = ast.Module(body=[a1, b1], type_ignores=[])
        code = compile(ast.parse(ast.unparse(module_func)), filename='fmu_eval', mode='exec')
        namespace = {"carray": carray, "event_n": event_n, "cfunc": cfunc, "types": types, "np": np, "len_q": len_q,
                     "q_ptr": q_ptr,
                     "component": component, "enter_event_mode": enter_event_mode, "set_time": set_time,
                     "get_event_indicators": get_event_indicators, "event_ind_call": event_ind_call,
                     "njit": njit,
                     "address_as_void_pointer": address_as_void_pointer,
                     "a_e_0": a_e_0,
                     "a_e_1": a_e_1,
                     "a_e_2": a_e_2,
                     "a_e_3": a_e_3,
                     "a_e_4": a_e_4,
                     "a_e_5": a_e_5,
                     "a_e_ptr_0": a_e_ptr_0,
                     "a_e_ptr_1": a_e_ptr_1,
                     "a_e_ptr_2": a_e_ptr_2,
                     "a_e_ptr_3": a_e_ptr_3,
                     "a_e_ptr_4": a_e_ptr_4,
                     "a_e_ptr_5": a_e_ptr_5,
                     "completedIntegratorStep": completedIntegratorStep}
        exec(code, namespace)
        event_action = namespace["event_action"]
        event_action_2 = namespace["event_action_2"]
        event_action_2.lines = ast.unparse(ast.Module(body=[b1], type_ignores=[]))

        self.t1 = self.create_namespace('t1')
        self.t1.add_equations([self])
        self.add_event("hitground_event", event_cond_2, event_action_2, compiled_functions={"event_cond": event_cond,
                                                                                            "event_action": event_action})

    @Equation()
    def eval(self, scope):
        scope.h_dot, scope.v_dot = self.fmu_eval(scope.h, scope.v, scope.g, scope.e)

    def set_variables(self, model_description):
        for variable in model_description.modelVariables:
            if variable.initial == 'exact':
                if variable.variability == 'fixed':
                    self.add_constant(variable.name, float(variable.start))
                if variable.variability == 'continuous':
                    self.add_state(variable.name, float(variable.start))
                if variable.variability == 'tunable':
                    self.add_parameter(variable.name, float(variable.start))


class Test_Eq(EquationBase):
    __test__ = False

    def __init__(self, T=0, R=1):
        super().__init__(tag='T_eq')
        self.add_state('Q', T)
        self.add_parameter('R', R)

    @Equation()
    def eval(self, scope):
        scope.Q_dot = scope.R + 9


class G(Item):
    def __init__(self, tag, TG, RG):
        super().__init__(tag)
        t1 = self.create_namespace('t1')
        t1.add_equations([Test_Eq(T=TG, R=RG)])


class S3(Subsystem):
    def __init__(self, tag):
        super().__init__(tag)

        fmu_filename = 'bouncingBall.fmu'
        fmu_subsystem = FMU_Subsystem(fmu_filename, "BouncingBall")
        fmu_subsystem2 = FMU_Subsystem(fmu_filename, "BouncingBall2")
        # fmu_subsystem3 = FMU_Subsystem(fmu_filename, "BouncingBall3", h=1.5)
        item_t = G('test', TG=10, RG=2)
        item_t.t1.R = fmu_subsystem.t1.h
        self.register_items([fmu_subsystem, fmu_subsystem2])


subsystem1 = S3('q1')
m1 = Model(subsystem1, use_llvm=True)
s = Simulation(
    m1, t_start=0, t_stop=1.0, num=500, num_inner=100, max_step=.1, solver_type=SolverType.NUMEROUS)
sub_S = m1.system.get_item(ItemPath("q1.BouncingBall"))
s.solve()
sub_S.fmu.terminate()

fig, ax = plt.subplots()
# t = np.linspace(0, 1.0, 100 + 1)
y = np.array(m1.historian_df["q1.BouncingBall.t1.h"])
y2 = np.array(m1.historian_df["q1.BouncingBall2.t1.h"])
# y3 = np.array(m1.historian_df["q1.BouncingBall3.t1.h"])
t = np.array(m1.historian_df["time"])
ax.plot(t, y)
ax.plot(t, y2)
# ax.plot(t, y3)

ax.set(xlabel='time (s)', ylabel='h', title='BB')
ax.grid()

plt.show()

print("execution finished")
