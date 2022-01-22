from numerous.engine.system import Item, VariableDescription
from numerous import VariableType
from numerous.multiphysics.equation_base import EquationBase
from numerous.multiphysics.equation_decorators import Equation
from numerous.engine.system import Subsystem

class Eqb(EquationBase,Item):
    def __init__(self):
        super(EquationBase).__init__()
        self.add_constant('qw', 5)
        test_namespace = self.create_namespace('namespace')
        var_desc = VariableDescription(tag='T', initial_value=0,
                                                   type=VariableType.PARAMETER)
        test_namespace.create_variable_from_desc(var_desc)
        test_namespace.add_equations([self])

    @Equation()
    def eval(self):
        self.k = self.k + 1

class Egg(Subsystem):
    def __init__(self):
        super().__init__("e")
        q=Eqb()
        self.register_items([q])
        print(self)

x=Egg()
