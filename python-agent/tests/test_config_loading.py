"""配置加载测试（Stage 15）。

不依赖真实密钥，也不读取开发者本机 .env：所有 .env 内容都用 tmp_path 构造，
并通过 _env_file 覆盖，确保与仓库根 .env 完全隔离。
"""

from pathlib import Path

from app.agents.settings import AgentSettings
from app.config import Settings


def test_both_settings_share_same_root_env_file():
    """修复核心：两个 Settings 类解析到同一份仓库根 .env（且 AgentSettings 不再为 None）。"""
    agent_ef = AgentSettings.model_config.get("env_file")
    svc_ef = Settings.model_config.get("env_file")
    assert agent_ef is not None, "AgentSettings 必须带 env_file（修复前为 None）"
    assert svc_ef is not None
    assert Path(agent_ef).resolve() == Path(svc_ef).resolve()
    # 路径基于文件位置，绝对且指向仓库根 .env，天然与 CWD 无关
    assert Path(agent_ef).resolve().name == ".env"


def test_settings_reads_unprefixed_key_from_env_file(tmp_path, monkeypatch):
    """Settings（无前缀）应从 .env 读取无前缀键。"""
    envf = tmp_path / ".env"
    envf.write_text("JAVA_BASE_URL=http://from-dotenv\n")
    monkeypatch.delenv("JAVA_BASE_URL", raising=False)
    s = Settings(_env_file=str(envf))
    assert s.java_base_url == "http://from-dotenv"


def test_process_env_overrides_env_file(tmp_path, monkeypatch):
    """优先级：进程环境变量 > .env 文件 > 代码默认值。"""
    envf = tmp_path / ".env"
    envf.write_text("JAVA_BASE_URL=http://from-dotenv\n")
    monkeypatch.setenv("JAVA_BASE_URL", "http://from-process")
    s = Settings(_env_file=str(envf))
    assert s.java_base_url == "http://from-process"


def test_agent_settings_reads_prefixed_keys_from_env_file(tmp_path, monkeypatch):
    """AgentSettings（前缀 clauseguard_agent_）应从 .env 读取带前缀键。"""
    envf = tmp_path / ".env"
    envf.write_text("CLAUSEGUARD_AGENT_LLM_MODEL=test-model-from-dotenv\n")
    monkeypatch.delenv("CLAUSEGUARD_AGENT_LLM_MODEL", raising=False)
    a = AgentSettings(_env_file=str(envf))
    assert a.llm_model == "test-model-from-dotenv"


def test_agent_settings_env_file_only_affects_prefixed_keys(tmp_path, monkeypatch):
    """AgentSettings 的 .env 只匹配带前缀键；其余字段回落默认值。"""
    envf = tmp_path / ".env"
    envf.write_text("CLAUSEGUARD_AGENT_LLM_BASE_URL=http://from-dotenv\n")
    monkeypatch.delenv("CLAUSEGUARD_AGENT_LLM_BASE_URL", raising=False)
    a = AgentSettings(_env_file=str(envf))
    assert a.llm_base_url == "http://from-dotenv"
    # 未在该 .env 提供的字段保持代码默认值（不被污染）
    assert a.llm_model == "gpt-4o-mini"
    assert a.model_backend == "live"
