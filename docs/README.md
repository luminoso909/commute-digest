# 通勤晨报

面向通勤时想了解不同领域动态的学生和上班族。每天北京时间 05:00 生成《通勤晨报》，17:00 生成《通勤晚报》。DeepSeek 联网搜索并整理双语解读。Python 从报道原页核对准确发布时间，在各期对应的 12 小时内选出 3–6 条。原页无法核对到具体时间的报道会被排除，核实不足 3 条时跳过该期并记录原因。网页可切换跨领域版和个人关注版并逐条朗读。

| 目录 | 内容 |
| --- | --- |
| `commute_digest/` | Python 网页、新闻编辑、投递服务及调度 |
| `macos/` | macOS 宿主应用和小组件 |
| `deploy/` | systemd 与 Caddy 部署模板 |
| `tests/` | Python 离线测试 |
| `docs/` | 使用说明和课堂汇报材料 |

## 一、本地运行

安装项目声明的 Python 版本和 `uv`，在项目根目录启动本地网页。SQLite 默认会在项目根目录的 `data/morning.sqlite3` 创建运行数据。

```bash
uv sync --locked
uv run python -m streamlit run commute_digest/web.py
```

需要从终端调用在线生成或实际投递时，先加载相应的 API 和邮箱配置。查看命令用法可运行 `uv run python -m commute_digest --help`。以下两条命令使用正式服务配置。

```bash
uv run python -m commute_digest publish
```

```bash
uv run python -m commute_digest deliver
```

`publish` 生成并保存内容。`deliver` 使用最近一个已到的 05:00 或 17:00 作为新闻窗口终点，通过 SMTP 发送订阅所选版本，并按该期的北京时间记录投递。网页逐条朗读对应语言的解读，语音格式为 WAV。

## 二、Linux 部署

部署目标为一台 Linux 主机，使用 systemd 管理 Streamlit 和定时任务，以 Caddy 提供 HTTPS 和一个阅读账号。以下配置使用专用服务用户 `commute-digest`，代码目录为 `/opt/commute-digest`，运行数据目录为 `/var/lib/commute-digest`。

以下为组内最终联调时执行的部署步骤。A 负责配置主机、域名与 HTTPS、DeepSeek API、SMTP 和真实收件邮箱。B 核对实际新闻与语音，C 核对浏览器和收件结果。三位成员共同完成归档中的验收记录。

1. 建立 `commute-digest` 用户及同名组。将项目置于 `/opt/commute-digest`，运行 `uv sync --locked --python /usr/bin/python3`，让服务用户能够读取代码和运行 `.venv`。系统 Python 版本需符合 `pyproject.toml`。
2. 从 `.env.example` 创建 `/etc/commute-digest.env`，设为 root 所有、权限 `0600`。填写 `DEEPSEEK_API_KEY`、`SMTP_HOST`、`SMTP_USER`、`SMTP_PASSWORD`、`RECIPIENT_EMAIL`。`SMTP_PORT` 默认为 `465`，使用隐式 TLS。发信地址 `SMTP_FROM` 缺省采用 `SMTP_USER`。
3. 将 `COMMUTE_DB` 设为 `/var/lib/commute-digest/morning.sqlite3`，`PUBLIC_BASE_URL` 设为实际的 `https://` 域名。systemd 会为服务创建运行数据目录。准备已经指向主机的 DNS，并允许 Caddy 使用 `80` 和 `443` 端口。
4. 使用 Caddy 2.8 或更新版本。执行 `caddy hash-password`，在交互提示中输入阅读密码。将 [Caddyfile 示例](../deploy/Caddyfile.example) 的站点块加入 `/etc/caddy/Caddyfile`，填写实际域名、一个用户名和生成的 bcrypt 哈希，再用 `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` 验证并重载 Caddy。[Caddy HTTPS](https://caddyserver.com/docs/automatic-https) · [Caddy 认证](https://caddyserver.com/docs/caddyfile/directives/basic_auth)
5. 配置真实演示邮箱并确认订阅后，从项目根目录启用服务和定时任务。定时任务在北京时间每日 05:00 和 17:00 启动，开机后等下一次触发。[systemd 时间格式](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html) · [systemd 定时任务](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)

```bash
sudo install -d -o commute-digest -g commute-digest -m 0750 /opt/commute-digest/commute_digest/static
sudo install -m 0644 deploy/commute-digest-web.service /etc/systemd/system/
sudo install -m 0644 deploy/commute-digest-send.service /etc/systemd/system/
sudo install -m 0644 deploy/commute-digest-send.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now commute-digest-web.service
sudo systemctl enable --now commute-digest-send.timer
systemctl list-timers commute-digest-send.timer
```

网页和投递进程都以 `commute-digest` 身份运行。由该用户写入 `commute_digest/static/` 下的标题快照，供网页服务读取。已有部署更新模板并执行 `daemon-reload` 后，重启 `commute-digest-web.service` 以应用新的网页入口。

## 三、最终联调时验证一次真实定时

先用正式网页保存演示偏好和邮件版式。检查最近的 05:00 或 17:00 对应期次尚未投递，再为定时器新建本次联调专用的覆盖文件 `/etc/systemd/system/commute-digest-send.timer.d/90-acceptance.conf`。将下面的日期和时间替换为同一时间窗口内、几分钟后的北京时间。

```ini
[Timer]
OnCalendar=
OnCalendar=YYYY-MM-DD HH:MM:00 Asia/Shanghai
Persistent=false
```

执行 `sudo systemctl daemon-reload` 和 `sudo systemctl restart commute-digest-send.timer`，用 `systemctl list-timers commute-digest-send.timer` 确认临时触发时刻。触发后查看 `journalctl -u commute-digest-send.service`，在真实邮箱打开当期链接。留存验收证据后移除本次新建的覆盖文件，再次加载配置、重启定时器，确认下次按北京时间 05:00 或 17:00 启动。

如果 SMTP 连接或登录失败，修复配置后可在下一期到来前执行 `sudo systemctl start commute-digest-send.service`，重试已生成的那一期。收件结果不确定时，先核对 SMTP 日志和收件箱。

## 四、AutoDL 定时投递

AutoDL 容器使用独立的 `screen` 会话运行调度进程。北京时间 05:00 的晨报覆盖前一日 17:00 之后至当日 05:00，17:00 的晚报覆盖当日 05:00 之后至 17:00。调度读取项目私有的 `.env` 后调用 `python -m commute_digest deliver --slot` 并传入相应的期次时间，与网页共用 SQLite。已成功发信的期次由投递记录去重。

升级已有 AutoDL 部署时，将服务器私有脚本 `.autodl/run-web.sh` 中的启动命令改为下面的入口。脚本继续在项目根目录执行并加载现有配置，ngrok 的转发目标仍为 `127.0.0.1:8501`。

```bash
exec .venv/bin/python -m streamlit run commute_digest/web.py \
  --server.address=127.0.0.1 --server.port=8501 \
  --server.headless=true --browser.gatherUsageStats=false
```

实例开机后，如调度会话需要恢复，先用 `screen -ls` 检查，再在项目目录执行以下命令。启动后会等待下一个 05:00 或 17:00。

```bash
screen -L -Logfile "$PWD/.autodl/daily.log" -dmS commute-digest-daily \
  "$PWD/.venv/bin/python" -u -m commute_digest.autodl_scheduler
tail -n 10 .autodl/daily.log
```

日志的 `next_run` 是下一次触发的北京时间。保持 AutoDL 实例、网页与 ngrok 会话运行，邮件回链才能在接收后继续访问。

## 五、离线检查

```bash
uv run pytest -q
```

## 六、课程与交接材料

演示顺序见 [课堂汇报框架](通勤晨报项目汇报框架.md)。原有完整规划、需求、验收记录、B/C 任务、B 交接说明和覆盖前备份保存在项目外的同级归档 `通勤晨报_资料归档_20261124`。真实运行和质量记录填写到归档的验收表。

## 七、Mac 桌面小组件

用 Xcode 打开 [Mac 项目](../macos/CommuteDigest.xcodeproj)，运行 `CommuteDigest`。首次打开宿主 App 时填写网页 HTTPS 地址、阅读账号和密码。然后在 Mac 桌面按住 Control 点击墙纸，选择「编辑小组件」，搜索「通勤晨报」并添加大号正方形组件。[Apple 的桌面小组件说明](https://support.apple.com/guide/mac-help/mchl52be5da5/mac)

小组件显示最近成功投送的正式一期。日期采用北京时间，标题沿用邮件的版式、语种和顺序。点击整个组件会用默认浏览器打开同一期。断网或新一期尚未投送时，会保留已有缓存及原日期。

标题快照由 Streamlit 在 `/app/static/widget-latest.json` 提供，沿用网页入口的认证。快照已生成但暂无正式投送期次时，接口返回 `null`。如需从 SQLite 的正式投递记录重建 `commute_digest/static/widget-latest.json`，可在项目根目录加载服务环境后运行 `uv run python -m commute_digest widget-feed`。此命令读取投递记录并更新标题快照。
