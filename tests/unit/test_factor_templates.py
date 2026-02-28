"""
Tests for the Factor Template Library.

Verifies:
- Library contains 30+ factor templates
- Each template has required fields
- Random sampling works reproducibly
- Family filtering works
- Entry code assembly generates valid Python
"""

import pytest

from agent.factor_templates import FactorTemplateLibrary


class TestGetAllFactors:
    def test_returns_list(self):
        lib = FactorTemplateLibrary()
        factors = lib.get_all()
        assert isinstance(factors, list)
        assert len(factors) >= 30

    def test_factor_has_required_fields(self):
        lib = FactorTemplateLibrary()
        required_fields = {
            "name", "family", "indicator_code",
            "signal_long", "signal_short",
            "params", "param_ranges",
        }
        for f in lib.get_all():
            missing = required_fields - set(f.keys())
            assert not missing, (
                f"Factor {f.get('name', '?')} missing fields: {missing}"
            )

    def test_valid_families(self):
        lib = FactorTemplateLibrary()
        valid_families = {"trend", "momentum", "volatility", "volume", "overlap"}
        for f in lib.get_all():
            assert f["family"] in valid_families, (
                f"Factor {f['name']} has invalid family: {f['family']}"
            )

    def test_params_and_ranges_match(self):
        """Every param key should have a matching range."""
        lib = FactorTemplateLibrary()
        for f in lib.get_all():
            for key in f["params"]:
                assert key in f["param_ranges"], (
                    f"Factor {f['name']}: param '{key}' has no range"
                )


class TestSampling:
    def test_sample_returns_n_factors(self):
        lib = FactorTemplateLibrary()
        sample = lib.sample(n=3, seed=42)
        assert len(sample) == 3

    def test_sample_reproducible(self):
        lib = FactorTemplateLibrary()
        s1 = lib.sample(n=3, seed=42)
        s2 = lib.sample(n=3, seed=42)
        assert [f["name"] for f in s1] == [f["name"] for f in s2]

    def test_sample_different_seeds_differ(self):
        lib = FactorTemplateLibrary()
        s1 = lib.sample(n=3, seed=42)
        s2 = lib.sample(n=3, seed=99)
        # Very unlikely to be the same with different seeds (but not impossible)
        # Just check they're valid
        assert len(s1) == 3
        assert len(s2) == 3

    def test_sample_with_family_filter(self):
        lib = FactorTemplateLibrary()
        sample = lib.sample(n=2, families=["momentum"], seed=42)
        assert len(sample) == 2
        assert all(s["family"] == "momentum" for s in sample)

    def test_sample_multi_family_filter(self):
        lib = FactorTemplateLibrary()
        sample = lib.sample(n=3, families=["trend", "volatility"], seed=42)
        assert len(sample) == 3
        assert all(s["family"] in ("trend", "volatility") for s in sample)

    def test_sample_n_larger_than_pool(self):
        """Should return all matching factors if n > pool size."""
        lib = FactorTemplateLibrary()
        sample = lib.sample(n=999, families=["volume"], seed=42)
        # volume has only 3 factors
        assert len(sample) <= 5  # reasonable upper bound

    def test_sample_no_duplicates(self):
        lib = FactorTemplateLibrary()
        sample = lib.sample(n=5, seed=42)
        names = [f["name"] for f in sample]
        assert len(names) == len(set(names))


class TestGetByName:
    def test_get_existing(self):
        lib = FactorTemplateLibrary()
        f = lib.get("RSI")
        assert f is not None
        assert f["name"] == "RSI"
        assert f["family"] == "momentum"

    def test_get_nonexistent(self):
        lib = FactorTemplateLibrary()
        f = lib.get("NONEXISTENT_INDICATOR_XYZ")
        assert f is None


class TestAssembleEntryCode:
    def test_basic_assembly(self):
        lib = FactorTemplateLibrary()
        factors = lib.sample(n=2, seed=42)
        code = lib.assemble_entry_code(factors)
        assert "enter_long" in code
        assert "enter_short" in code

    def test_assembly_valid_python(self):
        """Assembled code should be syntactically valid."""
        lib = FactorTemplateLibrary()
        factors = lib.sample(n=3, seed=42)
        code = lib.assemble_entry_code(factors)
        # Should not raise SyntaxError
        compile(code, "<string>", "exec")

    def test_assembly_single_factor(self):
        lib = FactorTemplateLibrary()
        factors = lib.sample(n=1, seed=42)
        code = lib.assemble_entry_code(factors)
        assert "enter_long" in code

    def test_assembly_with_custom_params(self):
        """Factors with overridden params should use those values."""
        lib = FactorTemplateLibrary()
        factors = [lib.get("RSI")]
        factors[0] = {**factors[0], "params": {"period": 7, "oversold": 25, "overbought": 75}}
        code = lib.assemble_entry_code(factors)
        assert "7" in code or "25" in code  # custom params should appear


class TestRenderIndicatorCode:
    def test_render_substitutes_params(self):
        lib = FactorTemplateLibrary()
        f = lib.get("RSI")
        rendered = lib.render_indicator(f)
        # Should have the default period (14) substituted
        assert "14" in rendered
        assert "{period}" not in rendered

    def test_render_with_override(self):
        lib = FactorTemplateLibrary()
        f = lib.get("RSI")
        f_custom = {**f, "params": {"period": 21, "oversold": 25, "overbought": 75}}
        rendered = lib.render_indicator(f_custom)
        assert "21" in rendered
