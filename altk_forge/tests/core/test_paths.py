import os

import pytest

from altk_forge.core import paths
from altk_forge.core.errors import InputError


def test_pipe_dir_is_under_the_pipelines_folder():
    assert paths.pipe_dir("my-pipe_1") == paths.PIPES / "my-pipe_1"


@pytest.mark.parametrize("pipeline_id", ["../escape", "a/b", "Upper", "", None, "-starts-with-dash", "x" * 65])
def test_pipe_dir_refuses_ids_that_could_escape_the_data_folder(pipeline_id):
    with pytest.raises(InputError, match="Invalid pipeline id"):
        paths.pipe_dir(pipeline_id)


def test_load_env_sets_only_what_is_not_set_yet(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nFORGE_T_NEW='quoted value'\nFORGE_T_SET=from-file\nnot a pair\n\n")
    monkeypatch.delenv("FORGE_T_NEW", raising=False)
    monkeypatch.setenv("FORGE_T_SET", "from-environment")
    paths.load_env(env)
    assert os.environ["FORGE_T_NEW"] == "quoted value"
    assert os.environ["FORGE_T_SET"] == "from-environment"
    monkeypatch.delenv("FORGE_T_NEW")


def test_load_env_without_a_file_does_nothing(tmp_path):
    paths.load_env(tmp_path / "missing.env")


def test_the_package_folder_holds_the_schemas_and_the_console():
    assert (paths.PACKAGE / "trajectory" / "schema" / "trajectory.schema.json").exists()
    assert (paths.PACKAGE / "web" / "index.html").exists()
