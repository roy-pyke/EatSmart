# EatSmart（本地运行指南）

FastAPI + SQLite 的营养记录后台，前端是同目录的 `index.html`。按下面步骤，即使电脑没装 Python 也能跑起来。

## 一键运行步骤（适合完全小白）
1) 安装 Python 3.10+  
   - Windows: 去 https://www.python.org/downloads/ 下载最新 3.x，安装时勾选 “Add python.exe to PATH”。  
   - macOS: 推荐用官方 pkg 安装包（同上下载页），或用 Homebrew：`brew install python`
2) 打开终端/命令行，进入项目目录  
   - Windows: 打开“命令提示符”或 PowerShell，运行 `cd 路径\\EatSmart`  
   - macOS: 打开“终端”，运行 `cd /Users/你的用户名/.../EatSmart`
3) 创建虚拟环境（隔离依赖）  
   ```bash
   python -m venv .venv
   ```
4) 激活虚拟环境  
   - Windows (PowerShell): `.\.venv\Scripts\Activate.ps1`  
   - Windows (CMD): `.\.venv\Scripts\activate.bat`  
   - macOS/Linux: `source .venv/bin/activate`
   激活后命令行前面会出现 `(.venv)`。
5) 安装依赖  
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
6) 启动后端服务（默认端口 8000）  
   ```bash
   uvicorn main:app --reload --port 8000
   ```
   看到 “Uvicorn running on http://127.0.0.1:8000” 即成功。
7) 打开前端  
   - 直接双击 `index.html` 用浏览器打开，或在项目目录运行 `python -m http.server 3000` 然后打开 http://localhost:3000/index.html  
   前端会调用本地的 `http://localhost:8000` 后端。

## 常用入口
- API 文档（自动生成）：http://localhost:8000/docs  
- 数据库存储：运行时会自动生成 `eatsmart.db`（SQLite 文件）。

## 数据模型速览
- Meals: 餐次（美西时区），可标记休息日/训练日，包含多个摄入条目。
- Food templates: 食物模板，按录入的基准克数保存营养表，支持收藏。
- Food intakes: 实际摄入（克），指向模板或自定义营养，记录时间戳。
- Day types: 每日的休息/训练标记，用来选择对应的营养目标。
- Weight logs: 体重历史，用于趋势和 BMI/BMR 计算。
- User profile: 身高、体重、年龄、性别。
- Nutrient goals: 每日营养上下限，按 day_type 区分（rest/training）。

## 典型操作
- 打开前端的“个人配置”设置身高体重、营养目标（切换休息日/训练日标签）。
- 在“用餐记录”添加餐次，选择日类型（休息/训练），可选模板或自定义食物。
- “仪表盘”查看日/周/月分析、趋势图，“导出 CSV”下载记录。
- 体重记录在“个人配置”页面新增/编辑/删除，右侧显示 30 天趋势。

## 故障排查
- 命令 `uvicorn` 找不到：确认虚拟环境已激活；或用 `python -m uvicorn main:app --reload --port 8000`
- 依赖安装慢：可换国内镜像（如 `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`）
- 端口被占用：换端口，例如 `uvicorn main:app --reload --port 9000`，前端 `index.html` 里的 `apiUrl` 一并改成新端口。

## 重要说明
- 默认使用美西时区（America/Los_Angeles）；传入的无时区时间会被视为美西时间。
- 所有数量以克为单位；自定义营养可指定基准克数，服务端会自动换算。
