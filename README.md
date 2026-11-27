# 通勤日报

通勤日报把过去 12 小时的新闻整理成 3–6 条双语报道。电脑和手机可以查阅个人往期、阅读详细解读、按需生成朗读。邮件提供摘要和原文出处，Mac 小组件可以直接打开网页。

| 我想做什么 | 从哪里开始 |
| --- | --- |
| 直接阅读 | [打开通勤日报](https://frosty-gizzard-chase.ngrok-free.dev/) → 登录或注册 |
| 在自己的服务器运行 | [配置 AutoDL 和公网](#配置-autodl-和公网) |
| 在自己电脑上生成、发信 | [本地部署](#本地部署) |
| 立即收到一封测试日报 | [一键调试投递](#一键调试投递) |
| 将日报放上 Mac 桌面 | [安装 Mac 小组件](#安装-mac-小组件) |

## 配置 AutoDL 和公网

AutoDL 负责网页、SQLite 和定时投递。电脑、手机和小组件都访问同一个 HTTPS 域名。ngrok 将请求转给 AutoDL 上的 `127.0.0.1:8501`。各客户端不需要连接 SSH 或开放多个端口。

**1. 准备配置。** 第一次运行时，启动向导会在云端终端询问下列参数。也可以参考仓库里的配置示例，直接在云端项目目录填写 `.env`。[配置示例](.env.example)

| 配置 | 填写内容 |
| --- | --- |
| `PUBLIC_BASE_URL` | ngrok 控制台 **Domains** 中分配给自己账号的域名，填完整的 `https://域名`。保留同一个域名，已有邮件和小组件就能继续打开。 |
| `NGROK_AUTHTOKEN` | ngrok 控制台 **Your Authtoken** 中的令牌。已有 `.autodl/ngrok.yml` 可直接复用。[ngrok 配置说明](https://ngrok.com/docs/start) |
| `DEEPSEEK_API_KEY` | 用于系统共享早晚报的服务端密钥。个人日报另在网页的「API 设置」填写读者自己的密钥。 |
| `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD` | 以 Gmail 为例，主机填 `smtp.gmail.com`，端口填 `465`，账号填自己的完整邮箱，密码填应用专用密码。项目使用隐式 TLS。[Gmail 应用专用密码说明](https://support.google.com/mail/answer/185833?hl=zh-Hans) |
| `SMTP_FROM` | 这台服务器的默认发件邮箱。首次部署可直接采用自己的 SMTP 登录邮箱；如果 SMTP 登录名不是邮箱，请另外填写服务商允许发信的地址。真实地址和授权码放在云端 `.env`，提交时只保留空白的 `.env.example`。 |
| `RECIPIENT_EMAIL` | 系统共享早晚报的收件邮箱。个人订阅和调试投递使用该读者在「登录信息」里保存的邮箱。 |
| `COMMUTE_DB` | 可选。默认使用项目目录下的 `data/morning.sqlite3`。 |

**2. 在云端启动。** 在 AutoDL 控制台打开一台能联网的 Linux 实例，使用控制台终端或 SSH 登录。下列命令适用于首次部署。已有项目从 `cd` 那一行开始。机器需要 Git、Python 3、Bash 和 GNU screen。启动器会准备 uv、ngrok 和项目依赖。

```bash
git clone https://github.com/luminoso909/commute-digest.git /root/autodl-tmp/commute-digest
cd /root/autodl-tmp/commute-digest
python3 deploy/一键启动.py
```

电脑已配置 SSH 别名 `autodl` 时，可以从电脑运行同一个向导。

```bash
ssh -t autodl 'cd /root/autodl-tmp/commute-digest && python3 deploy/一键启动.py'
```

**3. 检查公网。** 在云端项目目录执行 `python3 deploy/一键启动.py status`。网页、HTTPS 入口、共享早晚报、账号订阅应各运行一份，本机和公网均显示「可访问」。在电脑和手机浏览器分别打开输出的 HTTPS 地址。首次访问 ngrok 免费域名时可能需要点 **Visit Site**。断开 SSH 后服务会继续运行，实例关机期间无法投递。重开机后再次运行启动命令即可。

修改云端 `.env` 后运行 `python3 deploy/一键启动.py restart`。首次在空目录部署才需要创建配置文件。升级时复用原 `.env`、ngrok 配置和 SQLite。[备份、日志和普通 Linux 部署](docs/README.md)

## 本地部署

### 从本机连接 AutoDL 的同一份日报

若已有云端账号、密钥和 SMTP 配置，在自己的 Mac 终端保持下面的 SSH 连接，浏览器打开 [本机转发入口](http://127.0.0.1:18502)。网页实际运行在 AutoDL，登录、生成、归档和发信均使用原云端配置。本机只负责转发，免装 Python。首次从本机地址访问时，用原有云端账号登录。按 Ctrl+C 可关闭这条转发，云端服务继续运行。

```bash
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18502:127.0.0.1:8501 autodl
```

该命令使用已经配置好的 SSH 别名 `autodl`，本地监听地址仅对本机开放。手机和小组件仍可使用同一站点的公网 HTTPS 地址。

### 用 uv 在本机独立运行

本机可以完成生成、归档和邮件投递。只想浏览已有的云端日报，直接访问网页即可。本地运行会使用本机数据库，连接云端网站会使用云端账号和归档。

1. 安装 Git 和 uv。macOS 上已有 Homebrew 的读者可运行 `brew install uv`。项目支持 Python 3.11–3.14。[uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)
2. 在终端下载项目、安装依赖并启动网页。

   ```bash
   git clone https://github.com/luminoso909/commute-digest.git
   cd commute-digest
   uv sync --locked
   uv run python -m streamlit run src/commute_digest/web.py --server.address=127.0.0.1
   ```

3. 在本机浏览器打开 [本地日报](http://127.0.0.1:8501)。要发邮件，首次配置时将 `.env.example` 复制为项目根目录的 `.env`，填入上表的 SMTP 参数。在网页「登录信息」保存收件邮箱、「API 设置」保存个人密钥。停止刚启动的网页（Ctrl+C），再载入配置启动。

   ```bash
   uv run --env-file .env python -m streamlit run src/commute_digest/web.py --server.address=127.0.0.1
   ```

4. 需要定时发送时，在同一项目目录另开终端，按需启动对应进程。本地电脑须保持唤醒和联网。仅用网页点击「调试投递」时，运行网页进程即可。

   | 投递方式 | 运行命令 |
   | --- | --- |
   | 个人订阅，按各账号设置的时间投递 | `uv run --env-file .env python -m commute_digest.subscription_delivery` |
   | 系统共享早晚报，每天北京时间 05:00 和 17:00 | `uv run --env-file .env python -m commute_digest.autodl_scheduler`（需要 Bash） |

**用手机访问本地版。** 同一 Wi-Fi 下，把网页启动参数改为 `--server.address=0.0.0.0`，手机访问 `http://电脑局域网IP:8501`，并允许系统防火墙的本地网络访问。要在外网从邮件或 Mac 小组件打开 **这份本地数据库**，将自己的 HTTPS 隧道转发到这台电脑的 8501，填写对应的 `PUBLIC_BASE_URL`，保持电脑和隧道在线。例如已安装 ngrok 并配置令牌后，运行 `ngrok http 8501 --url https://分配给本机的域名`。云端和本机同时运行时使用不同的域名。个人邮件在未配置公网地址时仍可发送摘要和原文链接。

## 网页使用

1. **建立账号。** 在「登录 / 注册」选择「注册」，填写用户名和密码，记下系统生成的 8 位用户 ID。以后用 ID 和密码登录。用户名可以重名，个人日报按账号隔离。
2. **填写邮箱和模型。** 在「设置 → 登录信息」保存邮箱和头像。在「API 设置」选模型、输入密钥，可先测试连接，再点「保存」。使用通义千问编稿时，另在一个 DeepSeek 模型下保存个人密钥，供联网采集和朗读使用。侧栏徽标显示邮箱提供商，悬停可看全名，点击即可复制。
3. **发布与阅读。** 点「日报发布」生成并归档个人日报。在列表中按时间、期数、晨晚报或领域筛选。搜索框匹配当前语言的新闻标题。点「详细浏览」阅读正文、分析和出处。点「生成朗读」才合成语音，再点播放才会出声。
4. **订阅邮件。** 在「订阅设置」选择关注领域、关键词、邮件版式和语言，启用每日邮件投递并保存。晨报可选北京时间 05:00–16:59，晚报可选 17:00–次日 04:59。收件时间可以自定，选题窗口固定为上一期截止点到本期截止点的 12 小时。用收件账号打开个人邮件中的本期链接，就能回到对应报道。
5. **调整界面。** 「阅读设置」可切换版式与中英文。「界面设置」可切换主题色与日夜模式。设置窗口可拖动，点「返回」放弃当前页修改。刷新后仍可保持登录，在「登录信息」中可以退出。

## 一键调试投递

此功能默认关闭。部署者在网页进程使用的 `.env` 中设 `COMMUTE_DEBUG_DELIVERY=1`，并重启网页。AutoDL 使用上面的 `restart`，本机重新运行带 `--env-file .env` 的网页命令。

1. 登录后在「登录信息」保存收件邮箱，在「API 设置」保存个人密钥。个人定时订阅可以保持关闭。
2. 点侧栏 **调试投递**。系统用个人模型额度，按订阅偏好、邮件版式和语言编稿并发送真实邮件。取最新已截止的 12 小时，例如北京时间 14:00 点击会使用昨天下午 17:00 到今天早上 05:00 的窗口。
3. 页面显示「第 N 期试刊已由 SMTP 接收」后，去邮箱打开主题含「试刊」或「Preview」的邮件。用同一账号打开本期链接，可核对个人归档；小组件下次刷新时也能读取这期。需要另一份试刊，可在前一轮结束后再次点击。

**关闭调试。** 将开关改为 `0` 或移除该项并重启网页，侧栏随即隐藏按钮。正常订阅继续按原设置投递。

## 安装 Mac 小组件

适用于运行 macOS 14 或更新版本的 Apple 芯片和 Intel Mac。

1. 在网页的「设置 → 界面设置」下载安装包，或从 GitHub 直接下载。解压后打开「通勤日报小组件」文件夹，保持安装脚本和 `CommuteDigest.app` 放在一起，在 Finder 中双击 `安装小组件.command`。安装包内附编译好的应用，无需 Xcode。脚本会将宿主应用和小组件安装到 `~/Applications/CommuteDigest.app`，并打开连接设置。[下载 Mac 小组件 ZIP](https://github.com/luminoso909/commute-digest/releases/latest/download/CommuteDigest-macOS.zip)
2. 使用默认的项目公网地址，或改填自己部署的 HTTPS 站点。在宿主应用中输入该站点的 **8 位用户 ID 和密码**，点「验证并保存」。登录凭据保存在这台 Mac 的钥匙串中。
3. 按住 Control 点按桌面空白处，选择「编辑小组件」，搜索「通勤日报」并点击添加。安装程序会完成注册，桌面放置位置由你在这一步选定。[Apple 桌面小组件指南](https://support.apple.com/zh-cn/guide/mac-help/mchl52be5da5/mac)
4. 有个人投递后，小组件显示日期、晨晚报和新闻标题，点击打开相同站点的对应期。尚无报道时点击打开站点首页。浏览器使用自己的网页登录状态，首次访问私人报道时请用同一个日报账号登录。

安装包采用本地签名，未经过 Apple 公证。如果系统提示无法验证开发者，可从下方源码自行编译，或确认下载自本项目后，按 Apple 的说明在「系统设置 → 隐私与安全性」批准这份安装包。[Apple 的打开说明](https://support.apple.com/zh-cn/102445)

**从源码安装。** 下载或克隆仓库，安装含 macOS SDK 的完整 Xcode 并首次打开完成设置。然后在 Finder 中双击仓库的安装脚本。脚本会在本机编译，安装到相同位置。[源码安装脚本](macos/安装小组件.command)

要制作同样的分发包，在仓库根目录运行 `macos/安装小组件.command --package`，输出为 `dist/CommuteDigest-macOS.zip`。运行 Python 测试可执行 `uv run pytest -q`。项目架构、SQLite 数据归属和部署排查见[运行维护说明](docs/README.md)，课堂展示见[项目汇报框架](docs/通勤晨报项目汇报框架.md)。
