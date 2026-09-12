"""PAN world-model layer: learned counterfactual simulation of packing actions.

Sits AFTER the deterministic physics validator (`physics.validator.validate_layout`),
never replaces it. Physics decides feasibility; PAN imagines execution.
"""
