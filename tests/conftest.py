"""Pytest configuration and fixtures."""


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-phoenix",
        action="store_true",
        default=False,
        help="Run Phoenix sync tests (skipped by default)",
    )


def pytest_configure(config):
    """Override marker filter when --run-phoenix is passed."""
    if config.getoption("--run-phoenix"):
        # Remove the default marker filter to include phoenix tests
        # Get current markexpr and modify it
        markexpr = config.getoption("markexpr", default="")
        if markexpr == "not phoenix":
            config.option.markexpr = ""
        elif "not phoenix" in markexpr:
            # Remove "not phoenix" from the expression
            new_expr = markexpr.replace("not phoenix and ", "").replace(" and not phoenix", "").replace("not phoenix", "")
            config.option.markexpr = new_expr.strip()
