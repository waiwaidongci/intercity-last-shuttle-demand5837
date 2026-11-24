# 001 初始表结构

应用首次启动以 SQLAlchemy 模型创建表与唯一约束。现有数据表只补建缺失表，升级到修改过字段的版本需要显式迁移。

- `users` 账号角色和加盐密码摘要。
- `datasets` 不可变记录批次，以名称和 SHA-256 去重。
- `runs` 记录参数、数据批次、运行租约、分析结果、导出文件及复核决定。
- `schedules` 保存定时计划和下一到期时间。
- `deliveries` 保存站内分发回执，每次运行最多一个回执。
- `audit_events` 记录登录、数据入库、运行、复核和报表分发动作。

# 002 实到补报版本与备车方案回放

- `arrival_batches` 补报批次受理结果。同刻人数冲突整批拒收（`state=rejected`），拒收原因留痕，不写明细。
- `arrival_records` 按（运营日、线路、入账时刻）保留各版实到，唯一约束 `uq_arrival_moment` 防止同刻两个人数版本；实到可空表示缺测。
- `replays` 备车方案回放，按（目标晚、核账截点）唯一封存 `uq_replay_target_cutoff`；同参数重跑只累加 `reruns`，证据不足记 `status=failed` 且旧方案不受影响。
