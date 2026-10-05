# LanPulse

面向 6 英寸安卓手机的轻量局域网性能看板。一个容器同时提供网页、Beszel 数据代理、爱快路由器 SNMP 采集和设置页。

## 当前适配状态

- 已按现场 Beszel `0.13.2` 的 PocketBase API 字段实现，兼容 `systems.info` 中的新旧带宽字段。
- Beszel 地址预填为 `http://192.168.1.83:8090`；账号需在设置页填写。
- 爱快尚未就位，因此 SNMP 默认关闭。就位后在网页中填写地址、团体名并“发现接口”。
- 顶部显示北京时间、公历/农历日期及天气；天气默认使用北京坐标，可在设置页修改。
- 首页采用固定的 `1080×1920` 竖版逻辑画布，任何浏览器只做等比例缩放，不产生页面滚动；设置页可将整个仪表盘旋转 180°，适配充电口方向。

## 启动

```bash
cd /Users/cloudu/Hub/lanpulse
docker compose up -d --build
```

手机访问：`http://<Docker主机局域网IP>:18090`

设置页面：`http://<Docker主机局域网IP>:18090/settings`

首次进入设置无需 PIN。建议保存时设置至少 4 位管理 PIN。配置保存在 Docker 命名卷 `phone-performance-monitor-data` 中，更新或重新部署容器不会清除设置。

项目已更名为 **LanPulse**。为兼容原有部署，底层数据卷继续使用旧名称 `phone-performance-monitor-data`，因此重新部署不会丢失现有 Beszel、SSH、天气及画面设置。

## Beszel 设置

1. 建议在 Beszel 中新建只读用户供本项目使用。
2. 在设置页填写地址、邮箱和密码，先保存，再点“测试连接”。
3. 看板通过容器后端访问 Beszel，Beszel 密码不会返回手机浏览器。

Beszel agent 的常规状态数据本身约按分钟更新；页面与 WAN SNMP 数据流保持长连接，SNMP 可按 1–2 秒采样。若需要 Beszel 主机指标真正达到 1 秒粒度，需额外实现 PocketBase `rt_metrics` 实时订阅，本版本未伪称分钟数据为秒级数据。

## Ubuntu 训练输出

设置页可以配置 Ubuntu SSH 地址、用户名以及密码或 OpenSSH 私钥。监控服务每次执行一个应快速结束的只读日志命令，并将最近输出显示在首页终端区。建议为此单独创建低权限 SSH 用户。

systemd 服务可直接读取 journal，例如同时查看两个训练服务：

```bash
journalctl -n 80 -u training-a.service -u training-b.service --no-pager -o cat
```

从普通终端直接启动的进程无法被另一个容器事后可靠截获 stdout。启动训练时应同步写入日志文件：

```bash
python -u train.py 2>&1 | tee -a ~/training.log
```

设置页中的读取命令填写：

```bash
tail -n 80 ~/training.log
```

`-u` 用于关闭 Python stdout 缓冲，使训练输出及时写入文件。不要在设置中使用 `tail -F` 或 `journalctl -f`，因为读取命令需要在配置的超时前结束；页面默认约每 2 秒重新读取一次。SSH 密码、私钥和私钥口令只保存在 Docker 数据卷中，配置 API 只返回“是否已设置”，不会把原文返回浏览器。

## 爱快 SNMP 设置

在爱快管理界面启用 SNMP v2c，并把访问来源限制为运行本容器的主机 IP。设置页中填写：

- 路由器 LAN 地址和 UDP 161 端口；
- 单独生成的只读团体名；
- 保存后点击“发现接口”，选择实际 WAN 接口。

默认采用 IF-MIB 64 位计数器：

- 下载：`ifHCInOctets`，OID `1.3.6.1.2.1.31.1.1.1.6.{ifIndex}`
- 上传：`ifHCOutOctets`，OID `1.3.6.1.2.1.31.1.1.1.10.{ifIndex}`

如果爱快对 WAN 方向的定义与物理接口方向相反，可直接在高级设置中互换两个 OID。

## 固定仪表盘布局

首页没有标题栏和设置按钮，顶部显示北京时间、日期及天气，下面依次显示：

- 第一行：UBCLOUD（CPU、内存、GPU、系统负载），MACCLOUD（CPU、内存、温度、系统负载）。
- 第二行：SYNCLOUD（CPU、内存、温度、系统负载），PVECLOUD（CPU、内存、温度、系统负载）。
- 第三行：Ubuntu 训练终端，显示 SSH 日志命令的最新输出。
- 第四行左侧：爱快路由器上传、下载曲线；SNMP 未配置时保持缺省状态。
- 第四行右侧：四台设备各自的网络曲线。同一设备固定使用同一种颜色，下载为实线，上传为虚线。

设备按 Beszel 名称自动匹配，兼容新旧名称及 `Ubuntu`、`MacBook`、`NAS/Synology`、`PVE/Proxmox` 别名。设置页不在首页显示，可直接访问 `/settings`。

系统负载显示 Beszel 的 1 分钟 load average；指示条优先按系统逻辑线程数归一化。天气数据每 10–180 分钟缓存刷新，默认 30 分钟；外部天气服务短时不可用时会继续显示最近一次成功结果。

## 运维与安全边界

- 项目定位是可信局域网看板，不应直接映射到公网。
- HTTP 下管理 PIN、监控数据及终端输出未加密；跨不可信网络请在前面放置 HTTPS 反向代理。
- 配置文件权限设为 `0600`，但目前是明文持久化；保护好 `data` 卷和主机备份。
- 容器以非 root 用户运行、丢弃全部 Linux capabilities，并启用 `no-new-privileges`。

## 验证

```bash
python3 -m unittest discover -s tests -v
docker compose config
docker compose build
```

健康检查：`curl http://127.0.0.1:18090/api/health`

## 通过 Portainer 部署

适用于 Portainer 管理本机 Docker Standalone 的场景：

1. 进入目标环境，选择 **Stacks → Add stack → Repository**。
2. Stack name 填写 `lanpulse`。
3. Repository URL 填写 `https://github.com/CLOUDUH/lanpulse.git`。
4. Repository reference 选择 `main`，或填写 `refs/heads/main`。
5. Compose path 填写 `compose.yaml`。
6. 仓库为公开仓库，不需要启用 Authentication。
7. 不需要填写额外环境变量，也不需要启用 Relative path volumes。
8. 第一次部署建议关闭 GitOps 自动更新，点击 **Deploy the stack**。

从旧项目升级时，将现有 Stack 的 Repository URL 改为 `https://github.com/CLOUDUH/lanpulse.git` 后重新部署即可。服务与容器会改名为 `lanpulse`；如果 Portainer 提示旧容器占用 `18090` 端口，先停止并删除旧的 `phone-performance-monitor` 容器，再重新部署。不要删除 `phone-performance-monitor-data` 数据卷。

部署后确认容器 `lanpulse` 状态为 `healthy`，然后访问：

```text
仪表盘：http://<Docker主机局域网IP>:18090
设置页：http://<Docker主机局域网IP>:18090/settings
```

Docker 主机需要能够访问 GitHub、PyPI、Beszel 的 `192.168.1.83:8090/TCP`，以及路由器就位后的 SNMP `161/UDP`。如果宿主机的 `18090` 已被占用，只需修改 `compose.yaml` 中端口映射左侧的端口。

Portainer 通过 Agent 管理远程 Docker 主机时，较新的 Portainer 版本可能无法在远程环境执行 Compose 中的 `build`。这种场景应先通过 CI 构建并发布容器镜像，再将 Compose 改为引用 `image`。
