from __future__ import annotations

from typing import Any


SYSTEM_NAME = "MyInvestShadow"
SYSTEM_VERSION = "0.1.0"
SYSTEM_DESCRIPTION = "基于市场研究和主线研究结果生成影子账户仓位、净值曲线和调仓记录。"


def _endpoint(
    method: str,
    path: str,
    purpose: str,
    parameters: list[dict[str, Any]] | None,
    returns: str,
    read_only: bool,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "purpose": purpose,
        "parameters": parameters or [],
        "returns": returns,
        "read_only": read_only,
    }


def public_api_catalog(base_url: str) -> dict[str, Any]:
    groups = [
        {
            "key": "documentation",
            "name": "文档入口",
            "endpoints": [
                _endpoint(
                    "GET",
                    "/api",
                    "统一接口目录，说明公开接口和安全边界。",
                    [],
                    "接口目录 JSON，不触发重计算或写入。",
                    True,
                ),
                _endpoint(
                    "GET",
                    "/docs",
                    "FastAPI Swagger 文档。",
                    [],
                    "交互式 API 文档页面。",
                    True,
                ),
                _endpoint(
                    "GET",
                    "/redoc",
                    "FastAPI ReDoc 文档。",
                    [],
                    "ReDoc API 文档页面。",
                    True,
                ),
                _endpoint(
                    "GET",
                    "/openapi.json",
                    "OpenAPI 机器可读协议。",
                    [],
                    "OpenAPI JSON schema。",
                    True,
                ),
                _endpoint(
                    "GET",
                    "/",
                    "Web 首页。",
                    [],
                    "影子账户只读仪表盘 HTML。",
                    True,
                ),
            ],
        },
        {
            "key": "current_data",
            "name": "当前数据",
            "endpoints": [
                _endpoint(
                    "GET",
                    "/api/index",
                    "首页主要数据，适合 Web 首屏和外部概览读取。",
                    [],
                    "账户概览、仓位结构、市场约束、ETF 门禁、净值曲线、基准 ETF 对比线、调仓历史和来源状态。",
                    True,
                ),
                _endpoint(
                    "GET",
                    "/api/allocations/latest",
                    "最新影子目标仓位。",
                    [],
                    "最新运行记录、目标仓位列表、仓位层汇总和防御仓比例。",
                    True,
                ),
            ],
        },
        {
            "key": "history",
            "name": "历史数据",
            "endpoints": [
                _endpoint(
                    "GET",
                    "/api/nav",
                    "影子账户资金净值曲线。",
                    [],
                    "按基准日排序的净值、日收益、主动仓位和防御仓位。",
                    True,
                ),
                _endpoint(
                    "GET",
                    "/api/rebalance-history",
                    "按基准日去重的调仓历史。",
                    [],
                    "每次调仓的主动仓/防御仓变化、标的变化和净值变化。",
                    True,
                ),
            ],
        },
        {
            "key": "analysis_results",
            "name": "分析结果",
            "endpoints": [
                _endpoint(
                    "GET",
                    "/api/latest",
                    "最新影子账户完整状态和决策轨迹。",
                    [],
                    "最新运行记录、目标仓位、run_payload、decision_trace、ETF/个股/防御质量门禁细节、净值曲线、调仓历史和来源状态。",
                    True,
                ),
            ],
        },
        {
            "key": "system_status",
            "name": "系统状态",
            "endpoints": [
                _endpoint(
                    "GET",
                    "/health",
                    "健康检查。",
                    [],
                    "服务状态、是否已有运行记录、最新基准日和端口。",
                    True,
                ),
                _endpoint(
                    "GET",
                    "/api/source-status",
                    "上游来源最近快照状态。",
                    [],
                    "市场、主线、ETF、个股、龙头接口最近一次快照基准日、获取时间、成功状态和错误信息。",
                    True,
                ),
            ],
        },
        {
            "key": "operations",
            "name": "运维动作",
            "endpoints": [
                _endpoint(
                    "POST",
                    "/api/run/daily",
                    "手动拉取最新上游研究结果并生成影子仓位。",
                    [
                        {
                            "name": "X-Shadow-Token",
                            "in": "header",
                            "required": "仅在配置 SHADOW_API_TOKEN 或非本机监听时需要",
                            "description": "影子账户写入口令牌。",
                        }
                    ],
                    "新的影子账户运行结果；若上游失败、基准日不一致或已有刷新在运行，返回 409。",
                    False,
                )
            ],
        },
    ]
    total_endpoints = sum(len(group["endpoints"]) for group in groups)
    return {
        "system_name": SYSTEM_NAME,
        "version": SYSTEM_VERSION,
        "description": SYSTEM_DESCRIPTION,
        "base_url": base_url.rstrip("/"),
        "docs": {
            "swagger": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
        },
        "recommended_entrypoints": [
            {
                "path": "/api/index",
                "reason": "首页和外部看板首选，信息密度高且不含完整原始 payload。",
            },
            {
                "path": "/api/latest",
                "reason": "需要审计完整影子账户状态和决策轨迹时使用。",
            },
            {
                "path": "/api/allocations/latest",
                "reason": "只关心最新目标仓位时使用。",
            },
            {
                "path": "/api/nav",
                "reason": "只读取净值曲线时使用。",
            },
        ],
        "safety": [
            "/api 为只读说明目录，不触发重计算、写入、交易或同步。",
            "GET 接口只读取本地已生成状态，不做真实交易、不输出股数或金额。",
            "POST /api/run/daily 是唯一公开写入动作，只生成影子账户运行状态，不连接交易系统。",
            "系统忽略真实持仓，所有仓位均为影子组合百分比。",
            "上游失败、基准日不一致或门禁不足时按规则跳过或降级，不伪造仓位。",
        ],
        "groups": groups,
        "total_endpoints": total_endpoints,
    }
