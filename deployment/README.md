# GitHub 网站部署

网站：https://adamleopold.github.io/news-agent-standalone/

React 页面使用同一套组件、CSS、中英双语字典和实际分析记录。GitHub Pages
只托管静态资源；FastAPI、持续采集、SQLite 和 gpt-oss:20b 仍在原 Mac 运行。
仅改变网站路径和 API 地址，不替换模型或分析规则。

Pages 同时保存发布时的完整逐条新闻、每日新闻、事件、价格、旧数据、分析输入
及输出。在线 API 不可用时显示明确的快照提示；不会假装生成新的模型分析。
已生成的详情可继续查看；生成新简报/详情需要实时 API 在线。

完整 SQLite 压缩快照保存在 GitHub Release，含全部原表、版本、任务、心跳、
冻结预测及评估。解压到 `data/ttf_news.sqlite3` 可恢复相同状态，恢复前先停止
目标实例并备份已有数据库。数据清单和 SHA-256 见
`reports/github_snapshot_manifest.json`。第三方新闻保留短摘要与原文链接，价格
仍为 reference；这些资料不是已验证官方结算准确率。

## 本机公开 API

`./scripts/public-site start|status|stop` 只管理独立的 8766 适配进程，不重启原
8765 后端，也不停止 Ollama。适配层只开放本产品 API，保留 SSE，设置来源
限制、输入大小限制和队列上限；不会公开 Ollama 通用接口、文件或终端。

在获得公网入口授权后，执行 `./scripts/public-site enable-funnel`，只使用
HTTPS 10000 端口。`disable-funnel` 只关闭该端口，不重置其他 Tailscale 配置。
公网功能依赖 Mac 联网、原采集后端、模型、适配进程及 Tailscale 在线；关机
或休眠时网站继续显示快照，新分析暂停。GitHub Actions 不运行 20B 模型。

## 更新发布

1. `scripts/prepare-github.py` 在 `.runtime/github-publish` 准备独立发布目录，
   从在线 SQLite 备份生成一致快照，不修改原项目的 Git 历史。
2. 检查数据清单、测试和发布目录内容后推送独立仓库的 main。
3. GitHub Actions 构建并部署 Pages；新的完整 SQLite 快照作为 Release 附件。

七天验收沿用原基线和所有缺口记录。发布不等于七天通过。
