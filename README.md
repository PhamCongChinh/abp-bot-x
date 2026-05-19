# Playwright Chrome Profile Project

Dự án Playwright Python sử dụng **profile Chrome thật** trên máy (giữ nguyên cookies, session, extensions).

## Cấu trúc

```
abp-bot-x/
├── .venv/          # Virtual environment (Python 3.12)
├── main.py         # Script chính
├── requirements.txt
└── README.md
```

## Cài đặt

```bash
# Kích hoạt .venv
.venv\Scripts\activate

# Cài dependencies
pip install -r requirements.txt
```

## Chạy

> ⚠️ **Đóng toàn bộ Chrome trước khi chạy** — Playwright cần độc quyền truy cập user-data-dir.

```bash
.venv\Scripts\activate
python main.py
```

## Đổi profile

Trong `main.py`, sửa biến `CHROME_PROFILE`:

| Giá trị      | Ý nghĩa              |
|--------------|----------------------|
| `"Default"`  | Profile mặc định     |
| `"Profile 1"`| Profile thứ 2        |
| `"Profile 2"`| Profile thứ 3        |

Xem danh sách profile tại: `C:\Users\ThinkPad\AppData\Local\Google\Chrome\User Data\`

## Lỗi thường gặp

**TargetClosedError** → Chrome đang mở. Đóng hết Chrome rồi chạy lại.
"# abp-bot-x" 
