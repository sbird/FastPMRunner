"""
A class to automate the fastpm simulation
"""
from typing import Tuple

import os
import json
import subprocess
import numpy as np

from .make_pklin import save_powerspec
from .lua_template import simple_lua_string

class SimulationICs(object):
    """
    Class for creating the initial conditions for a fastpm simulation.

    There are a few things this class needs to do:
    - Generate linear theory input files (use python/make-pklin.py)
    - Generate fastpm .lua parameter file (ref: tests/nobodykit.lua)
    - Run fastpm simulation directly and generate power spectrum

    The class will store the parameters of the simulation.
    We also save a copy of the input and enough information to reproduce the
    results exactly in SimulationICs.json.
    Many things are left hard-coded.

    Init parameters:
    ----
    outdir     - Directory in which to save ICs
    box        - Box size in comoving Mpc/h. Bigger simulation box particles have
                 less interactions, smaller box the particle interactions will be
                 more intense.
    npart      - Cube root of number of particles. This controls the resolution
                 of a N-body simulation.
    redshift   - redshift at which to generate ICs
    omegab     - baryon density.
    omegam     - Total matter density at z=0. (omega_m = omega_b + omega_cdm)
    hubble     - Hubble parameter, h, which is H0 / (100 km/s/Mpc)
    scalar_amp - A_s at k = 0.05, comparable to the Planck value.
    ns         - Scalar spectral index
    timesteps  - number of time steps for the simulation
    """

    def __init__(
        self,
        outdir: str = "nbodykit",
        param_file: str = "param.lua",
        box: int = 384,
        npart: int = 128,
        seed: int = 100,
        redshift: float = 99,
        redend: float = 0,
        omega0: float = 0.288,
        omegab: float = 0.0472,
        hubble: float = 0.7,
        scalar_amp: float = 2.427e-9,
        ns: float = 0.97,
        fastpm_bin: str = "fastpm",
        timesteps: float = 10,
        cores: int = 4,
    ) -> None:

        self.outdir = outdir
        self.param_file = param_file

        # Check that input is reasonable and set parameters
        # In Mpc/h
        assert box < 20000
        self.box = box

        # Cube root
        assert npart > 1 and npart < 16000
        self.npart = int(npart)

        # Physically reasonable
        assert omega0 <= 1 and omega0 > 0
        self.omega0 = omega0

        assert omegab > 0 and omegab < 1
        self.omegab = omegab

        assert redshift > 1 and redshift < 1100
        self.redshift = redshift

        assert redend >= 0 and redend < 1100
        self.redend = redend

        # start/end time in scale factor
        self.time_start = 1 / (1 + self.redshift)
        self.time_end = 1 / (1 + self.redend)

        self.timesteps = timesteps

        assert hubble < 1 and hubble > 0
        self.hubble = hubble

        assert scalar_amp < 1e-7 and scalar_amp > 0
        self.scalar_amp = scalar_amp

        assert ns > 0 and ns < 2
        self.ns = ns

        self.seed = seed

        # the folder to store simulation outputs
        if not os.path.exists(self.outdir):
            os.mkdir(self.outdir)

        self.fastpm_bin = fastpm_bin
        self.cores = cores

    def make_pklin(self, outfile: str = "my_pk_linear.txt") -> None:
        """
        Make linear power spectrum and save as a file
        """
        # save into the same folder as simulation output
        self.linear_file = os.path.join(self.outdir, outfile)

        save_powerspec(
            omega0=self.omega0,
            omegab=self.omegab,
            hubble=self.hubble,
            scalar_amp=self.scalar_amp,
            ns=self.ns,
            outfile=self.linear_file,
        )

    def make_simulation(
        self,
        write_runpb_snapshot: bool = False,
        write_snapshot: bool = False,
        write_fof: bool = False,
    ) -> Tuple[str, str]:
        """
        Generate .lua input parameter file for fastpm simulation
        """
        if "linear_file" not in dir(self):
            self.make_pklin()

        self.write_powerspectrum = os.path.join(self.outdir, "powerspec")

        lua_string = simple_lua_string(
            box=self.box,
            npart=self.npart,
            seed=self.seed,
            omega0=self.omega0,
            omegab=self.omegab,
            hubble=self.hubble,
            scalar_amp=self.scalar_amp,
            ns=self.ns,
            time_start=self.time_start,
            time_end=self.time_end,
            timesteps=self.timesteps,
            read_powerspectrum=self.linear_file,
            write_powerspectrum=self.write_powerspectrum,
            write_runpb_snapshot=write_runpb_snapshot,
            write_snapshot=write_snapshot,
            write_fof=write_fof,
        )

        with open(os.path.join(self.outdir, self.param_file), "w") as f:
            f.write(lua_string)

        # run FastPM
        bash_command = "mpirun -n {cores} {fastpm_bin} {param_file}".format(
            cores=self.cores,
            fastpm_bin=self.fastpm_bin,
            param_file=os.path.join(self.outdir, self.param_file),
        )
        print(bash_command.split())

        #Single threading for fastpm
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = "1"
        process = subprocess.run(bash_command.split(), check=True, env=env, capture_output=True)
        output = process.stdout
        # write the output
        with open(os.path.join(self.outdir, "message.out"), "w") as f:
            f.write(output.decode('utf-8'))

        # write parameters into a json file
        self.to_json()

        # set power spec into variables
        self.set_powerspec()

        return output, process.stderr

    def set_powerspec(self):
        """Set power spectrum"""
        self._scale_factors = np.linspace(self.time_start, self.time_end, self.timesteps)

        powerspec_fn = lambda scale_factor: "{}_{:.4f}.txt".format(self.write_powerspectrum, scale_factor)

        self._powerspecs = []
        self._kk = []
        # load the powerspecs
        for scale_factor in self._scale_factors:
            kk, pk, modes = np.loadtxt(powerspec_fn(scale_factor)).T

            self._kk.append(kk)
            self._powerspecs.append(pk)

        self._kk = np.array(self._kk)
        self._powerspecs = np.array(self._powerspecs)

    @property
    def kk(self):
        return self._kk

    @property
    def powerspecs(self):
        return self._powerspecs

    @property
    def scale_factors(self):
        return self._scale_factors

    def to_json(self):
        with open(os.path.join(self.outdir, "SimulationICs.json"), 'w') as jsout:
            json.dump(self.__dict__, jsout)
