# ⚗️ 科研配方量纲校验台（dimcheck）

录入**带单位的计算表达式**与变量取值，系统解析括号、幂运算与函数调用，**递归推导量纲**并自动完成单位换算；对**相加异量纲**、**非法幂指数**、**未定义变量**等错误，给出**指向表达式具体片段**的定位信息，并展示 SI 换算结果。还可为部分直接测量的变量填写**带单位的标准不确定度**，系统以各独立测量输入为扰动源，对完整变量链与主表达式做**双侧小步长重算**，按一阶平方和（GUM RSS）给出结果的**合成标准不确定度**。支持校验案例的**保存 / 加载 / 删除**与 **JSON 导出**。

技术栈：Python 3.11+ · FastAPI · SQLite · 原生 HTML/JS/CSS（无前端构建）。

---

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
docker compose up --build
```

启动后访问：

- Web 界面：<http://localhost:8000>
- API 文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI 描述：<http://localhost:8000/openapi.json>
- 健康检查：<http://localhost:8000/api/health>

服务容器内固定监听 **8000 端口**；SQLite 数据存储在命名卷 `dimcheck-data` 中，容器重建不丢失。需要改用宿主机其他端口时，设置环境变量后再启动（例如宿主机 8080）：

```bash
APP_PORT=8080 docker compose up --build
```

### 方式二：本地运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

同样可用 `--port` 指定其他端口。

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

---

## 表达式语法

| 特性 | 示例 |
| --- | --- |
| 四则运算 / 括号 | `(1 + 2) * 3` |
| 幂运算（右结合，也可用 `**`） | `v^2`、`2^3^2 = 512`、`m^-2` |
| 数字带单位 | `9.8 m/s^2`、`1.5e-3 mol`、`2.5 kJ` |
| 隐式乘法 | `2 m`、`kg m/s^2`、`2(3+4)`、`2π r` |
| Unicode 上标 | `m²`、`x⁻¹`、`5 m³` |
| 符号别名 | `× ÷ · −`（等价于 `* / * -`） |
| 函数 | `sqrt(4 m^2)`、`sin(30 deg)`、`ln(e)`、`log(100)`、`abs(-3 m)` |
| 变量 | 变量值本身是表达式，可相互引用：`F = mass * g0` |
| 内置常量 | `pi π e c g0 G kb NA R qe me eps0 mu0` |

**名称解析顺序**：用户变量 > 内置常量 > 内置单位。变量名与单位/常量同名时按变量解析，并返回非致命**警告**（`warnings` 字段），避免静默错误。

**量纲规则**：

- 加 / 减：两边量纲必须一致，否则报 `dim_mismatch`，错误片段指向右操作数；
- 乘 / 除：量纲指数相应加减，数值自动换算到 SI；
- 幂：指数必须**无量纲**（否则 `bad_exponent`，片段指向指数），底数量纲指数乘以指数（允许分数，如 `(4 m^2)^0.5 = 2 m`）；
- `sin / exp / ln` 等函数参数必须无量纲（否则 `dimless_arg`）；`sqrt` 使量纲指数减半；
- 未定义的名称报 `undefined`，片段指向该标识符；变量循环引用报 `var_cycle`。

完整单位列表见 Web 界面右侧「单位 · 常量 · 函数」面板或 `GET /api/units`。注意：摄氏温度 °C 等带偏移的单位不支持，请使用开尔文 K。

---

## 测量不确定度分析

在变量行第三列（`u` 列）为**部分直接输入的变量**填写**带单位的标准不确定度**即可，留空表示该输入无不确定度；其他变量仍可通过表达式引用它们，不确定度沿完整变量链自动传播。不确定度本身也是带单位表达式（如 `2 cm + 3 mm`）。

后端处理流程：

1. **校验**：标准不确定度必须非负（标准差含义），且量纲必须与对应变量输入完全一致，否则返回 `uncertainty_value` / `uncertainty_dim` 错误；为不存在的变量填写时返回 `uncertainty_unknown`。
2. **双侧小步长重算**：以每个正不确定度的独立测量输入为扰动源，取中心差分步长 `h = 1e-5 · max(|x|, u)`——**标称值 x 为零时以 u 为尺度**，保证步长仍然有效；对 `x±h` 分别重算**完整变量链与主表达式**。
3. **一阶平方和**：第 i 个输入对结果的贡献为 `cᵢ = |f(x+h) − f(x−h)| / (2h) · uᵢ`，合成标准不确定度 `u_c = sqrt(Σ cᵢ²)`（各输入视为相互独立）。
4. **失败即停止**：标称计算或任一正/负方向扰动遇到量纲、定义域（如 `sqrt` 负数、`ln(0)`、`asin` 超界、除零）或非有限值错误时，返回 `uncertainty_eval` 错误并**指出对应输入**，立即停止汇总，不返回合成不确定度数值，避免给出貌似有效的结果。
5. **并列显示与一起换算**：页面并列展示标称值与 `±u_c`；选择目标单位时两者按同一换算因子一起换算（另附相对不确定度 `u_c/|y|`，标称值为零时为 `null`）。

响应中 `result.uncertainty` 还包含每个扰动源的标称值、标准不确定度、实际步长、对结果的贡献与平方和占比（`sources`），以及经变量链传播后每个变量的标称值与合成标准不确定度（`variables`）。未填写任何不确定度时该字段缺省；全部填零时 `si_value = 0`。

> 若某个**定义式引用了其他变量**的变量也单独指定了标准不确定度，系统会按独立扰动源处理，并给出 `uncertainty_independent` 警告：它可能与其上游变量的不确定度被重复计入，建模时通常只应为真正的直接测量输入填写。

### 不确定度示例

```bash
curl -s http://localhost:8000/api/check -H 'Content-Type: application/json' -d '{
  "expression": "0.5*mass*v^2 + mass*g0*h0",
  "variables": {"mass": "2 kg", "v": "10 m/s", "h0": "5 m"},
  "uncertainties": {"mass": "0.01 kg", "v": "0.2 m/s", "h0": "0.05 m"},
  "target_unit": "kJ"
}'
```

结果（节选）：标称 `198.0665 J`，合成标准不确定度 `u_c ≈ 4.2359 J`（相对约 2.14%），目标单位下 `0.19807 kJ ± 0.0042359 kJ`；`sources` 中可见 `v` 的贡献最大（约 4.0 J）。

---

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/check` | 校验表达式，返回量纲、SI 数值、合成标准不确定度、目标单位换算、错误/警告 |
| GET | `/api/units` | 单位 / 常量 / 函数目录 |
| POST | `/api/cases` | 保存校验案例 |
| GET | `/api/cases` | 案例列表 |
| GET | `/api/cases/{id}` | 读取案例 |
| DELETE | `/api/cases/{id}` | 删除案例 |
| GET | `/api/export` | 导出全部案例（JSON 附件下载） |
| GET | `/api/health` | 健康检查 |

### 示例：校验通过

```bash
curl -s http://localhost:8000/api/check -H 'Content-Type: application/json' -d '{
  "expression": "0.5*mass*v^2 + mass*g0*h0",
  "variables": {"mass": "2 kg", "v": "10 m/s", "h0": "5 m"},
  "uncertainties": {"mass": "0.01 kg", "v": "0.2 m/s", "h0": "0.05 m"},
  "target_unit": "kJ"
}'
```

`POST /api/check` 请求字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `expression` | string | 主表达式 |
| `variables` | object | 变量名 → 取值表达式（带单位，可相互引用） |
| `uncertainties` | object | 变量名 → 该直接输入的**标准不确定度**表达式（带单位，可只给部分变量） |
| `target_unit` | string \| null | 目标单位；标称值与不确定度一起换算 |

```json
{
  "ok": true,
  "errors": [],
  "warnings": [],
  "result": {
    "si_value": 198.0665,
    "dimension": ["2", "1", "-2", "0", "0", "0", "0"],
    "dimension_names": ["L", "M", "T", "I", "Θ", "N", "J"],
    "dimension_str": "L^2·M·T^-2",
    "si_unit": "m^2·kg·s^-2",
    "derived": ["J"],
    "target": {"unit": "kJ", "value": 0.1980665, "uncertainty": 0.00423585437},
    "uncertainty": {
      "si_value": 4.23585437,
      "si_unit": "m^2·kg·s^-2",
      "relative": 0.02138602,
      "step_factor": 1e-05,
      "sources": [
        {"name": "mass", "nominal_si": 2.0, "uncertainty_si": 0.01, "step_si": 2e-05, "contribution_si": 0.9903325},
        {"name": "v", "nominal_si": 10.0, "uncertainty_si": 0.2, "step_si": 0.0001, "contribution_si": 4.0},
        {"name": "h0", "nominal_si": 5.0, "uncertainty_si": 0.05, "step_si": 5e-05, "contribution_si": 0.980665}
      ],
      "variables": [
        {"name": "mass", "si_value": 2.0, "uncertainty_si": 0.01, "dimension_str": "M", "si_unit": "kg"},
        {"name": "v", "si_value": 10.0, "uncertainty_si": 0.2, "dimension_str": "L·T^-1", "si_unit": "m·s^-1"},
        {"name": "h0", "si_value": 5.0, "uncertainty_si": 0.05, "dimension_str": "L", "si_unit": "m"}
      ]
    }
  }
}
```

### 示例：错误定位（相加异量纲）

```bash
curl -s http://localhost:8000/api/check -H 'Content-Type: application/json' -d '{
  "expression": "1 m + 1 s"
}'
```

```json
{
  "ok": false,
  "errors": [{
    "message": "量纲不一致：「L」与「T」不能相加",
    "start": 6, "end": 9,
    "kind": "dim_mismatch",
    "source": "expression"
  }],
  "result": null
}
```

`start` / `end` 是原始表达式字符串中的字符区间（上例即片段 `"1 s"`），`source` 取值：`expression`（主表达式）、`var:<名称>`（某变量的定义式）、`unc:<名称>`（该变量的标准不确定度输入，或由它引发的扰动失败）、`target`（目标单位）。前端据此将出错片段高亮标红。

### 错误类型（`kind`）

| kind | 含义 |
| --- | --- |
| `syntax` | 语法错误（空表达式、括号不配对、多余内容等） |
| `undefined` | 未定义的变量或单位 |
| `dim_mismatch` | 加减法两边量纲不一致 |
| `bad_exponent` | 幂指数不是无量纲量 |
| `dimless_arg` | 函数参数要求无量纲 |
| `arity` | 函数参数个数错误 |
| `var_error` / `var_cycle` | 变量定义出错 / 循环引用 |
| `target_dim` | 目标单位与结果量纲不一致 |
| `uncertainty_dim` | 标准不确定度与对应输入量纲不一致 |
| `uncertainty_value` | 标准不确定度为负或非有限值 |
| `uncertainty_unknown` | 为变量列表中不存在的变量填写了不确定度 |
| `uncertainty_eval` | 标称或扰动重算触发量纲/定义域/数值错误，已停止汇总 |
| `value` | 除零、定义域、非有限值等数值错误 |

### 保存与导出案例

```bash
# 保存（uncertainties 可省略或只含部分变量；旧库会自动迁移补齐该列）
curl -s -X POST http://localhost:8000/api/cases -H 'Content-Type: application/json' -d '{
  "name": "自由落体", "expression": "0.5*g0*t^2",
  "variables": {"t": "3 s"}, "uncertainties": {"t": "0.05 s"},
  "target_unit": "m"
}'

# 导出全部案例为 JSON 文件
curl -OJ http://localhost:8000/api/export
```

---

## 项目结构

```
├── app/
│   ├── main.py        # FastAPI 应用：API 路由 + 静态页面
│   ├── engine.py      # 引擎：词法/语法分析 → 量纲求值 → 双侧差分不确定度传播
│   ├── units.py       # 单位 / 常量 / 函数注册表，量纲格式化
│   ├── db.py          # SQLite 案例存储（含 uncertainties 列自动迁移）
│   └── static/        # 原生前端（index.html / app.js / style.css）
├── tests/             # pytest：引擎单元测试 + API 集成测试
├── Dockerfile
├── docker-compose.yml
├── requirements.txt       # 运行依赖
└── requirements-dev.txt   # 测试依赖
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | `./data/app.db`（容器内 `/app/data/app.db`） | SQLite 数据库文件路径 |
| `APP_PORT` | `8000`（仅 Docker Compose） | 宿主机映射端口；容器内服务始终监听 8000 |
