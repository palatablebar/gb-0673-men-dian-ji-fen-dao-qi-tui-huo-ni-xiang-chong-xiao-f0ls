# 深度登记 门店积分到期与退货逆向冲销

规模：中型。使用者：连锁零售会员运营专员。核心规则：消费先用最早到期批次；退款按原消费分配逆向恢复，已过期部分不得复活；旧门店与新门店两路来源合并成一套可用余额，来源差异与新版退货证据保留可查。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 事件采信 | 执收旧格式或合并格式事件，保留来源 | `POST /api/intake` | `app/modules/intake/reader.py` | `tests/test_intake.py` |
| M2 权益规则 | 校验事件，跨来源同事件去重 | `POST /api/policy` | `app/modules/policy/rules.py` | `tests/test_policy.py` |
| M3 积分归批 | 按来源建批 | `POST /api/lots` | `app/modules/lots/rules.py` | `tests/test_lots.py` |
| M4 消费到期 | 重放消费、退款与到期 | `POST /api/consumption` | `app/modules/consumption/rules.py` | `tests/test_consumption.py` |
| M5 退货回拨 | 回拨审计并核验新门店依据 | `POST /api/refund` | `app/modules/refund/rules.py` | `tests/test_refund.py` |
| M6 余额对账 | 拆来源对账并跑发布闸门 | `POST /api/reconcile` | `app/modules/reconcile/rules.py`、`app/release.py` | `tests/test_reconcile.py`、`tests/test_release.py` |
| M7 期末归档 | 归档并签发发布版本 | `POST /api/archive` | `app/modules/archive/rules.py`、`app/store.py` | `tests/test_archive.py`、`tests/test_release.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | GB18030权益中文 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_gb18030_real` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_real` |
| TRAP-08 | 会员前导零 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_member_leading_zero` |
| TRAP-04 | 重复表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_repeated_header` |
| TRAP-05 | 缺列记录 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_column_bad` |
| TRAP-14 | 编号冲突 | 静默错算或证据丢失 | 同来源内按业务键判冲突，不同来源允许同号 | `app/modules/intake/reader.py` | `tests/test_intake.py::test_duplicate_conflict` |
| TRAP-18 | 空态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_empty_three` |
| TRAP-07 | 哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_sentinel_real` |
| TRAP-11 | UTC归一 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_utc_real` |
| TRAP-06 | 全角积分 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_fullwidth_points` |
| TRAP-23 | 权益半开窗 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_rule_end_exclusive` |
| TRAP-24 | 旧格式缺新字段 | 压平后旧记录被判缺列丢失 | 旧列必填、新列可选，旧记录补 `legacy_compat` 而非补造证据 | `app/modules/intake/reader.py` | `tests/test_release.py::test_legacy_layout_compatible`、`test_legacy_records_not_lost_by_new_fields` |
| TRAP-25 | 两路重复投递 | 同一积分记两次 | 标准化后按业务键去重，被并行留隔离证据 | `app/modules/policy/rules.py` | `tests/test_release.py::test_cross_source_duplicate_only_counted_once` |
| TRAP-26 | 新版退货证据缺失 | 压平后渠道/原因不可追 | 逐笔核验渠道、原因、原消费，缺失阻断发布 | `app/modules/refund/rules.py` | `tests/test_release.py::test_missing_reason_blocks_publish` 等 |
| TRAP-27 | 中断发布 | 新旧版本同时生效或新账写一半 | 单事务先落新版再退旧版，中断回滚保留旧版 | `app/store.py` | `tests/test_release.py::test_interrupted_publish_keeps_old_release` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

发布闸门四项（行守恒、积分守恒、退货回拨算术、新门店退货依据）全部通过才可发布；凭证缺陷只阻断发布，不改写余额。来源账按旧门店、新门店、合计三行展示，三行逐字段闭合。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。本次不展开新权益设计或会员账户合并。
