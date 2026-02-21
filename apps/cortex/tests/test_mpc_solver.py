"""Tests for the OSQP MPC solver."""

import numpy as np
import pytest

from src.mpc.solver import MPCSolver, MPCSolveResult, MPCConfig
from src.mpc.reference import ReferenceGenerator, CostWeights
from src.mpc.model import GrowRoomModel, NX, NU, InputIndex


class TestMPCSolver:
    @pytest.fixture
    def solver(self):
        return MPCSolver()

    @pytest.fixture
    def ref_gen(self):
        return ReferenceGenerator()

    @pytest.fixture
    def default_problem(self, solver, ref_gen):
        """Standard test problem setup."""
        x0 = solver.model.default_state()
        u0 = solver.model.default_input()
        ref = ref_gen.generate_reference(12.0, solver.Np, solver.dt)
        d_forecast = ref_gen.generate_disturbance_forecast(12.0, solver.Np, solver.dt)
        return x0, u0, ref, d_forecast

    def test_solve_returns_result(self, solver, default_problem):
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        assert isinstance(result, MPCSolveResult)

    def test_solve_feasible(self, solver, default_problem):
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        assert result.feasible, f"Solver status: {result.status}"

    def test_solve_control_in_bounds(self, solver, default_problem):
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        assert np.all(result.u_optimal >= 0.0)
        assert np.all(result.u_optimal <= 1.0)

    def test_solve_control_shape(self, solver, default_problem):
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        assert result.u_optimal.shape == (NU,)

    def test_solve_has_trajectory(self, solver, default_problem):
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        if result.feasible:
            assert result.predicted_trajectory is not None
            assert result.predicted_trajectory.shape == (solver.Np, NX)

    def test_solve_timing(self, solver, default_problem):
        """Solve time should be reasonable (< 500ms even on slow machines)."""
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        assert result.total_ms < 500, f"Solve took {result.total_ms:.1f}ms"

    def test_warm_start_faster(self, solver, default_problem):
        """Warm-started solve should be faster than cold start."""
        x0, u0, ref, d = default_problem
        cold = solver.solve(x0, d, ref, u0)
        warm = solver.solve(x0, d, ref, cold.u_optimal)
        # Warm start should use fewer iterations
        if cold.feasible and warm.feasible:
            assert warm.iterations <= cold.iterations

    def test_fan_minimum_speed(self, solver, default_problem):
        """Fan should maintain minimum speed (0.2)."""
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        if result.feasible:
            assert result.u_optimal[InputIndex.FAN] >= 0.2 - 0.01

    def test_mutual_exclusion_cool_heat(self, solver, default_problem):
        """Cooling and heating should not both be high simultaneously."""
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        if result.feasible:
            cool = result.u_optimal[InputIndex.COOL]
            heat = result.u_optimal[InputIndex.HEAT]
            assert cool + heat <= 1.01  # small tolerance

    def test_mutual_exclusion_hum_dehum(self, solver, default_problem):
        """Humidifier and dehumidifier should not both be high simultaneously."""
        x0, u0, ref, d = default_problem
        result = solver.solve(x0, d, ref, u0)
        if result.feasible:
            hum = result.u_optimal[InputIndex.HUM]
            dehum = result.u_optimal[InputIndex.DEHUM]
            assert hum + dehum <= 1.01

    def test_solve_with_none_u_prev(self, solver, ref_gen):
        """Solve should work when u_prev is not provided."""
        x0 = solver.model.default_state()
        ref = ref_gen.generate_reference(12.0, solver.Np, solver.dt)
        d = ref_gen.generate_disturbance_forecast(12.0, solver.Np, solver.dt)
        result = solver.solve(x0, d, ref, u_prev=None)
        assert isinstance(result, MPCSolveResult)

    def test_solve_short_forecast(self, solver, ref_gen):
        """Solver should handle forecast shorter than horizon by padding."""
        x0 = solver.model.default_state()
        u0 = solver.model.default_input()
        ref = ref_gen.generate_reference(12.0, solver.Np, solver.dt)
        d_short = ref_gen.generate_disturbance_forecast(12.0, 5, solver.dt)
        result = solver.solve(x0, d_short, ref, u0)
        assert isinstance(result, MPCSolveResult)

    def test_reset_clears_warmstart(self, solver, default_problem):
        x0, u0, ref, d = default_problem
        solver.solve(x0, d, ref, u0)
        assert solver._last_solution is not None
        solver.reset()
        assert solver._last_solution is None
        assert not solver._initialized


class TestMPCConfig:
    def test_default_config(self):
        cfg = MPCConfig()
        assert cfg.Np == 30
        assert cfg.Nc == 10
        assert cfg.dt == 60.0

    def test_custom_config(self):
        cfg = MPCConfig(Np=20, Nc=5, max_iter=100)
        assert cfg.Np == 20
        assert cfg.Nc == 5
        assert cfg.max_iter == 100


class TestSolverSpeed:
    """Benchmark tests for solve time targets."""

    def test_cold_start_under_100ms(self):
        """Cold start should complete in < 100ms."""
        solver = MPCSolver()
        ref_gen = ReferenceGenerator()
        x0 = solver.model.default_state()
        u0 = solver.model.default_input()
        ref = ref_gen.generate_reference(12.0, solver.Np, solver.dt)
        d = ref_gen.generate_disturbance_forecast(12.0, solver.Np, solver.dt)

        result = solver.solve(x0, d, ref, u0)
        assert result.total_ms < 100, f"Cold start: {result.total_ms:.1f}ms"

    def test_warm_start_under_50ms(self):
        """Warm-started solve should complete in < 50ms."""
        solver = MPCSolver()
        ref_gen = ReferenceGenerator()
        x0 = solver.model.default_state()
        u0 = solver.model.default_input()
        ref = ref_gen.generate_reference(12.0, solver.Np, solver.dt)
        d = ref_gen.generate_disturbance_forecast(12.0, solver.Np, solver.dt)

        # Cold start to prime warm-start
        cold = solver.solve(x0, d, ref, u0)
        # Warm start
        warm = solver.solve(x0, d, ref, cold.u_optimal)
        assert warm.total_ms < 50, f"Warm start: {warm.total_ms:.1f}ms"

    def test_linearize_under_5ms(self):
        """Linearization should complete in < 5ms."""
        solver = MPCSolver()
        ref_gen = ReferenceGenerator()
        x0 = solver.model.default_state()
        u0 = solver.model.default_input()
        ref = ref_gen.generate_reference(12.0, solver.Np, solver.dt)
        d = ref_gen.generate_disturbance_forecast(12.0, solver.Np, solver.dt)

        result = solver.solve(x0, d, ref, u0)
        assert result.linearize_ms < 5, f"Linearize: {result.linearize_ms:.1f}ms"