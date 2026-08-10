from __future__ import annotations

import os
import time
from dataclasses import dataclass

from app.llm.gateway import LLMGateway, SchemaExtractionFailed
from app.schemas.job_profile import JobProfile

SAMPLE_REQUIREMENT = "要个做嵌入式开发的，能写驱动，最好懂 AUTOSAR"

EXTRACTION_SYSTEM_PROMPT = (
    "你是招聘助手，把业务经理的口语化用人需求转成结构化岗位画像 JSON，"
    "字段需符合给定 Schema，缺失信息用合理默认值填充，不要编造具体项目经验。"
)


@dataclass
class ProviderConfig:
    name: str
    api_key_env: str
    base_url: str
    model: str
    supports_json_schema: bool


@dataclass
class ComparisonResult:
    provider_name: str
    schema_valid: bool
    latency_ms: float
    raw_output: str
    error: str | None
    skipped: bool = False


@dataclass
class ComparisonSummary:
    recommended_provider: str
    disqualified: list[str]
    skipped: list[str]
    results: list[ComparisonResult]


# 候选供应商，来自 01-开源调研与技术选型.md 的候选名单。
# deepseek：0.1 实测结果（2026-08-09，见 docs/m1-model-comparison.md）——
#   GET /v1/models 返回的真实模型只有 deepseek-v4-flash / deepseek-v4-pro，
#   不存在原先写死的 deepseek-chat-241226；response_format=json_schema 被
#   拒绝（400 "This response_format type is unavailable now"），json_object
#   实测通过且响应 model 字段原样回显请求的别名（该字段本身证明不了版本没变，
#   见 docs/m1-model-comparison.md 的 system_fingerprint 说明）。
#   deepseek-v4-flash 同日追加对比（M1 demo 是同步等待场景，pro 单次延迟
#   ~9.4s 太慢），json_schema 支持情况沿用 pro 的实测结论，未独立重测。
# doubao / qwen：未实测（无 API key，账号还没注册），模型名与 json_schema
#   支持情况仍是候选阶段的占位猜测，接线前必须实测。
PROVIDER_CANDIDATES: list[ProviderConfig] = [
    ProviderConfig(
        name="deepseek-pro",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-pro",
        supports_json_schema=False,
    ),
    ProviderConfig(
        name="deepseek-flash",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-flash",
        supports_json_schema=False,
    ),
    ProviderConfig(
        name="doubao",
        api_key_env="ARK_API_KEY",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seed-2-1-turbo-241215",  # 未实测（无 API key）——实测前请去火山方舟控制台确认 Endpoint ID / 版本号
        supports_json_schema=True,
    ),
    ProviderConfig(
        name="qwen",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-plus-241226",  # 未实测（无 API key）——实测前请去 DashScope 控制台确认当前可用的锁定版本号
        supports_json_schema=False,
    ),
]


def run_comparison(
    sample_text: str, providers: list[ProviderConfig]
) -> list[ComparisonResult]:
    results = []
    for provider in providers:
        api_key = os.environ.get(provider.api_key_env, "")
        if not api_key:
            results.append(
                ComparisonResult(
                    provider_name=provider.name,
                    schema_valid=False,
                    latency_ms=0.0,
                    raw_output="",
                    error=f"跳过：环境变量 {provider.api_key_env} 未设置",
                    skipped=True,
                )
            )
            continue
        gateway = LLMGateway(
            api_key=api_key,
            base_url=provider.base_url,
            model=provider.model,
            supports_json_schema=provider.supports_json_schema,
            max_retries=0,  # 对比测试只看首次是否达标，不吃重试红利
        )
        started = time.monotonic()
        try:
            profile = gateway.extract_structured(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_prompt=sample_text,
                schema=JobProfile,
            )
            latency_ms = (time.monotonic() - started) * 1000
            results.append(
                ComparisonResult(
                    provider_name=provider.name,
                    schema_valid=True,
                    latency_ms=latency_ms,
                    raw_output=profile.model_dump_json(),
                    error=None,
                )
            )
        except SchemaExtractionFailed as exc:
            latency_ms = (time.monotonic() - started) * 1000
            results.append(
                ComparisonResult(
                    provider_name=provider.name,
                    schema_valid=False,
                    latency_ms=latency_ms,
                    raw_output="",
                    error=str(exc),
                )
            )
    return results


def summarize(results: list[ComparisonResult]) -> ComparisonSummary:
    active = [r for r in results if not r.skipped]
    passing = [r for r in active if r.schema_valid]
    disqualified = [r.provider_name for r in active if not r.schema_valid]
    skipped = [r.provider_name for r in results if r.skipped]

    if not passing:
        raise ValueError("没有供应商通过 Schema 校验，需要人工排查或换供应商")

    best = min(passing, key=lambda r: r.latency_ms)
    return ComparisonSummary(
        recommended_provider=best.provider_name,
        disqualified=disqualified,
        skipped=skipped,
        results=results,
    )


if __name__ == "__main__":
    results = run_comparison(SAMPLE_REQUIREMENT, PROVIDER_CANDIDATES)
    summary = summarize(results)
    print(f"推荐供应商: {summary.recommended_provider}")
    print(f"未通过 Schema 校验: {summary.disqualified}")
    print(f"跳过（无 API key）: {summary.skipped}")
    for r in results:
        if r.skipped:
            print(f"- {r.provider_name}: skipped ({r.error})")
        else:
            print(f"- {r.provider_name}: schema_valid={r.schema_valid} latency={r.latency_ms:.0f}ms")
