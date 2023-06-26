"""
Provide a smooth interface between .cu files and python code.
"""

import os
import inspect
import warnings
from dataclasses import dataclass, field
import cupy as cp

from os import PathLike
from typing import Callable, Iterable, Iterator, Collection, Mapping, TypeVar, TypeVarTuple, Generic, Self, Any

CUDA_DEVICE_COUNT: int = cp.cuda.runtime.getDeviceCount()
CUDA_THREAD_LIMIT      = tuple[int, ...](cp.cuda.runtime.getDeviceProperties(i)["maxThreadsPerBlock"] for i in range(CUDA_DEVICE_COUNT))
CUDA_BLOCK_SHAPE       = tuple[tuple[int, int, int], ...](cp.cuda.runtime.getDeviceProperties(i)["maxThreadsDim"] for i in range(CUDA_DEVICE_COUNT))
CUDA_GRID_SHAPE        = tuple[tuple[int, int, int], ...](cp.cuda.runtime.getDeviceProperties(i)["maxGridSize"] for i in range(CUDA_DEVICE_COUNT))


@dataclass(slots=True)
class Dim:

    x: int = field(default=1)
    y: int = field(default=1)
    z: int = field(default=1)

    @property
    def tuple(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)
    
    def __iter__(self) -> Iterator[int]:
        def iterator():
            yield self.x
            yield self.y
            yield self.z
        return iterator()
    
    def __getitem__(self, idx: int) -> int:
        return getattr(self, ("x", "y", "z")[idx])
    
    def __setitem__(self, idx: int, value: int):
        setattr(self, ("x", "y", "z")[idx], value)

KernelArgs = TypeVarTuple("KernelArgs")
DimFun     = Callable[[*KernelArgs], tuple[Dim, Dim]]
RawKernel  = Callable[[Dim, Dim, tuple[*KernelArgs]], None]


class CudaModule(type):

    def __new__(cls: type[Self],
                name: str,
                bases: tuple[type, ...],
                namespace: Mapping[str, object],
                *,
                path: PathLike,
                defines: Iterable[tuple[str, Any]] = (),
                cpp_standard: str = "c++11",
                path_lexically_relative: bool = True):
        ret = super().__new__(cls, name, bases, namespace)

        if path_lexically_relative and not os.path.isabs(path):
            caller = inspect.stack()[1]
            dirname = os.path.dirname(caller.filename)
            path = os.path.join(dirname, path)

        with open(path) as file:
            code = file.read()
        
        options = (f"-std={cpp_standard}",)
        options = options + tuple(f"-D{name}=({value})" for name, value in defines)

        kernels = [k for k in namespace.values() if isinstance(k, CudaKernel)]
        name_expressions = [x for k in kernels for x in k.get_names()]

        ret.module = cp.RawModule(code=code, name_expressions=name_expressions, options=options)

        for k in kernels:
            k.set_pfuns(ret)

        return ret
    
    def get_function(self: Self, name: str) -> RawKernel:
        return self.module.get_function(name)

class CudaKernel(Generic[*KernelArgs]):

    def __init__(self: Self,
                 dimspec: DimFun[*KernelArgs] | tuple[Dim, Dim],
                 templates: Iterable[str] | None = None,
                 templatespec: Callable[[*KernelArgs], str] = lambda *_: ""):
        if callable(dimspec):
            self.dims = dimspec
        else:
            self.dims = lambda *_: dimspec
        self.name: str | None = None
        self.templates = templates
        self.templatespec = templatespec
        self.pfuns = dict[str, RawKernel[*KernelArgs]]()

    def __set_name__(self, owner: CudaModule, name: str):
        self.name = name

    def get_names(self: Self) -> Iterable[str]:
        if self.templates is None:
            return (self.name,)
        else:
            return (self.name + template for template in self.templates)
    
    def set_pfuns(self: Self, owner: type[CudaModule]):
        if self.templates is None:
            self.pfuns[""] = owner.get_function(self.name)
        else:
            for template in self.templates:
                self.pfuns[template] = owner.get_function(self.name + template)

    def __call__(self: Self, *args: *KernelArgs) -> None:
        gridspec, blockspec = self.dims(*args)
        template = self.templatespec(*args)
        from mpi4py import MPI
        self.pfuns[template](gridspec.tuple, blockspec.tuple, args)

    
class DimSpec:

    x, y, z = 0, 1, 2
    
    @classmethod
    def one_thread(cls: type[Self]) -> DimFun[*KernelArgs]:
        return lambda *_: (Dim(), Dim())

    @classmethod
    def groupby(cls: type[Self], dim: int, arg: int = 0, shape: Callable[[tuple[int, ...]], Dim] = lambda x: Dim(*x)) -> DimFun[*KernelArgs]:
        def ret(*args: *KernelArgs) -> tuple[Dim, Dim]:
            s = shape(args[arg].shape)
            dev: int = cp.cuda.runtime.getDevice()
            thread_lim = min(CUDA_THREAD_LIMIT[dev], CUDA_BLOCK_SHAPE[dev][dim]) // 2
            gridspec, blockspec = list(s), [1] * 3
            blocks = (lambda d: d[0] + (1 if d[1] else 0))(divmod(s[dim], thread_lim))
            gridspec[dim] = blocks
            gridspec = Dim(*gridspec)
            if blocks > CUDA_GRID_SHAPE[dev][dim]:
                raise NotImplementedError(f"Grid {gridspec} too large for CUDA device {dev}")
            blockspec[dim] = thread_lim
            return gridspec, Dim(*blockspec)
        return ret
    
    @classmethod
    def groupby_first_x(cls: type[Self], shape: Callable[[tuple[int, ...]], Dim] = lambda x: Dim(*x)) -> DimFun[*KernelArgs]:
        return cls.groupby(cls.x, 0, shape)


class Requires(object):

    def __init__(self: Self,
                 generator: Callable[..., Iterable[str]],
                 t: TypeVar,
                 *ss: TypeVar):
        super().__init__()

        self.generator = generator
        self.t = t
        self.ss = ss        

    @property
    def name(self: Self) -> str:
        return self.t.__name__
    
    @property
    def depends(self: Self) -> Iterable[str]:
        return (s.__name__ for s in self.ss)
    
    @classmethod
    def DoubleOrComplex(cls: type[Self], t: TypeVar) -> Self:
        return Requires(lambda: ("double", "complex<double>"), t)
    
    @classmethod
    def ModulusOf(cls: type[Self], t: TypeVar, s: TypeVar) -> Self:
        lookup = {
            "float": "float",
            "double": "double",
            "complex<float>": "float",
            "complex<double>": "double"
        }
        return Requires(lambda arg: (lookup[arg],) if arg in lookup else (), t, s)

class TemplateSpec:

    numpy_to_cpp = {
        cp.dtype(cp.float32): "float",
        cp.dtype(cp.float64): "double",
        cp.dtype(cp.complex64): "complex<float>",
        cp.dtype(cp.complex128): "complex<double>"
    }

    @classmethod
    def array_dtype(cls: type[Self], *params: int) -> Callable[[*KernelArgs], str]:
        return lambda *args: '<' + ", ".join(cls.numpy_to_cpp[args[i].dtype] for i in params) + '>'


def parse_template(*args: TypeVar | Requires) -> list[str]:
    typevars = list[str]()
    requires = list[Requires]()
    for arg in args:
        if isinstance(arg, TypeVar):
            typevars.append(arg.__name__)
        else:
            requires.append(arg)
    requires.sort(key=lambda req: typevars.index(req.name))

    templates: list[tuple[str]] = [()]
    for req in requires:
        deps = [typevars.index(d) for d in req.depends]
        ext = lambda tvars: req.generator(*(tvars[i] for i in deps))
        templates = [tvars + (v,) for tvars in templates for v in ext(tvars)]
    return templates

class _CudaKernelDecorator:
    def __call__(self, dimspec: DimFun[*KernelArgs]) -> Callable[[Callable[[*KernelArgs], None]], CudaKernel[*KernelArgs]]:
        return lambda _: CudaKernel(dimspec)
    def __getitem__(self, args) \
        -> Callable[[DimFun[*KernelArgs]], Callable[[Callable[[*KernelArgs], None]], CudaKernel[*KernelArgs]]]:
        templates = ['<' + ", ".join(tvars) + '>' for tvars in parse_template(*args)]
        return lambda dimspec, templatespec: (lambda _: CudaKernel(dimspec, templates, templatespec))
cuda_kernel = _CudaKernelDecorator()
