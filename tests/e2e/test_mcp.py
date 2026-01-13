import os
import pytest
import json
from fastmcp.client import Client
from pathlib import Path
from dotenv import load_dotenv

__data__ = Path(__file__).parent.parent / 'data'
load_dotenv()

@pytest.fixture
def mcp():
    os.environ['KAIZEN_NAMESPACE_ID'] = 'test'
    from kaizen.frontend.client.kaizen_client import KaizenClient
    from kaizen.config.kaizen import kaizen_config
    # we change the namespace ID for these tests so we have to reset the loaded settings
    kaizen_config.__init__()
    kaizen_client = KaizenClient()
    # ensure clean test environment
    kaizen_client.delete_namespace('test')
    from kaizen.frontend.mcp.mcp_server import mcp
    yield mcp
    kaizen_client.delete_namespace('test')

@pytest.mark.e2e
async def test_save_trajectory_and_retrieve_guidelines(mcp):
    async with Client(transport=mcp) as kaizen_mcp:
        trajectory = (__data__ / 'trajectory.json').read_text()
        response = await kaizen_mcp.call_tool_mcp('save_trajectory', {
            'trajectory_data': trajectory,
            'task_id': '123'
        })
        saved_trajectory = json.loads(response.content[0].text)
        # MCP server should return entity versions of the trajectory
        assert len(saved_trajectory) == 19
        response = await kaizen_mcp.call_tool_mcp('get_guidelines', {
            'task': 'What states do I have teammates in? Read the list from the states.txt file. use the filesystem mcp tool'
        })
        guidelines = response.content[0].text
        assert '# Guidelines for: ' in guidelines