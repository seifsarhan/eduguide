import os
import time
import requests
from flask import render_template, request, jsonify, session, redirect, url_for
from core.db import get_db, resolve_student_numeric_id
from core.security import login_required, log_audit


def get_paymob_auth_token():
    api_key = os.getenv("PAYMOB_API_KEY")
    if not api_key:
        return None
    resp = requests.post("https://accept.paymob.com/api/auth/tokens", json={"api_key": api_key.strip()}, timeout=15)
    return resp.json().get("token")


def initiate_paymob_order(token, amount_cents, merchant_order_id=None):
    payload = {
        "auth_token": token,
        "delivery_needed": "false",
        "amount_cents": str(amount_cents),
        "currency": "EGP",
        "items": [],
    }
    if merchant_order_id:
        payload["merchant_order_id"] = str(merchant_order_id)
    resp = requests.post("https://accept.paymob.com/api/ecommerce/orders", json=payload, timeout=15)
    return resp.json().get("id")


def get_payment_key(token, order_id, amount_cents, integration_id, user_info):
    payload = {
        "auth_token": token,
        "amount_cents": str(amount_cents),
        "expiration": 3600,
        "order_id": str(order_id),
        "billing_data": {
            "apartment": "NA",
            "email": user_info.get("email", "student@eduguide.com"),
            "floor": "NA",
            "first_name": user_info.get("first_name", "Student"),
            "street": "NA",
            "building": "NA",
            "phone_number": user_info.get("phone", "01000000000"),
            "shipping_method": "NA",
            "postal_code": "NA",
            "city": "Cairo",
            "country": "EG",
            "last_name": user_info.get("last_name", "User"),
            "state": "NA",
        },
        "currency": "EGP",
        "integration_id": integration_id,
    }
    resp = requests.post("https://accept.paymob.com/api/acceptance/payment_keys", json=payload, timeout=15)
    return resp.json().get("token")


def register_payment_routes(app):
    @app.route("/payments")
    @login_required
    def payments_page():
        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            payments = []
            if nid:
                cursor.execute("SELECT * FROM payments WHERE user_id=%s ORDER BY id DESC", (nid,))
                payments = cursor.fetchall()
        finally:
            db.close()
        return render_template("payments.html", payments=payments)

    @app.route("/payments/confirm", methods=["POST"])
    @login_required
    def payments_confirm():
        allow_demo = os.getenv("ALLOW_DEMO_PAYMENT_CONFIRM", "true").strip().lower() in ("1", "true", "yes")
        if not allow_demo and session.get("role") == "student":
            return jsonify({"success": False, "error": "Direct demo payment confirmation is disabled in production. Use Paymob checkout."}), 403

        data = request.get_json(silent=True) or {}
        pid = data.get("payment_id")
        method = (data.get("method") or "Card").strip()

        db = get_db()
        cursor = db.cursor()
        try:
            nid = resolve_student_numeric_id(cursor, session["user_id"])
            # Ownership verification (IDOR protection)
            cursor.execute("SELECT * FROM payments WHERE id=%s", (pid,))
            payment = cursor.fetchone()
            if not payment:
                return jsonify({"success": False, "error": "Payment not found"}), 404
            if session.get("role") == "student" and str(payment["user_id"]) != str(nid):
                log_audit("PAYMENT_CONFIRM_BLOCKED", "payment", pid, "IDOR attempt blocked")
                return jsonify({"success": False, "error": "Forbidden"}), 403

            cursor.execute(
                "UPDATE payments SET payment_status='paid', payment_date=CURDATE(), payment_method=%s WHERE id=%s",
                (method, pid),
            )
            user_code = session.get("user_id")
            if user_code:
                cursor.execute(
                    "UPDATE facpayments SET status='Paid', payment_date=CURDATE(), payment_method=%s WHERE student_id=%s ORDER BY payment_id DESC LIMIT 1",
                    (method, user_code),
                )
            db.commit()
            log_audit("PAYMENT_CONFIRMED", "payment", pid, f"Payment #{pid} marked paid via {method}")
        finally:
            db.close()
        return jsonify({"success": True})

    @app.route("/receipt/<int:payment_id>")
    @login_required
    def receipt(payment_id):
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT * FROM payments WHERE id=%s", (payment_id,))
            payment = cursor.fetchone()
            if not payment:
                return redirect(url_for("payments_page"))

            # Ownership check for students (IDOR protection)
            if session.get("role") == "student":
                nid = resolve_student_numeric_id(cursor, session["user_id"])
                if str(payment["user_id"]) != str(nid):
                    return redirect(url_for("payments_page"))
        finally:
            db.close()
        return render_template("receipt.html", payment=payment)

    @app.route("/payments/initiate", methods=["POST"])
    @login_required
    def initiate_payment():
        if not os.getenv("PAYMOB_API_KEY"):
            return jsonify({"success": False, "error": "Please add PAYMOB_API_KEY to your .env file"}), 400

        data = request.get_json(silent=True) or {}
        amount = float(data.get("amount", 0))
        payment_id = data.get("payment_id")
        method = data.get("method", "card")

        amount_cents = int(amount * 100)
        merchant_order_id = f"EDY_{payment_id}_{int(time.time())}"

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT * FROM vw_studentprofile WHERE student_id=%s", (session["user_id"],))
            user = cursor.fetchone() or {}
        finally:
            db.close()

        token = get_paymob_auth_token()
        if not token:
            return jsonify({"success": False, "error": "Invalid Paymob API Key. Please check your credentials."}), 400

        order_id = initiate_paymob_order(token, amount_cents, merchant_order_id)
        if not order_id:
            return jsonify({"success": False, "error": "Order Creation Failed. Check your connection."}), 400

        integration_id = os.getenv("PAYMOB_INTEGRATION_ID_CARD") if method == "card" else os.getenv("PAYMOB_INTEGRATION_ID_FAWRY")
        if not integration_id:
            return jsonify({"success": False, "error": f"Integration ID for {method} is missing in .env"}), 400

        payment_token = get_payment_key(token, order_id, amount_cents, integration_id, user)
        if method == "card":
            iframe_id = os.getenv("PAYMOB_IFRAME_ID", "1039430")
            redirect_url = f"https://accept.paymob.com/api/acceptance/iframes/{iframe_id}?payment_token={payment_token}"
            return jsonify({"success": True, "redirect_url": redirect_url})

        pay_resp = requests.post(
            "https://accept.paymob.com/api/acceptance/payments/pay",
            json={"source": {"identifier": "AGGREGATOR", "subtype": "AGGREGATOR"}, "payment_token": payment_token},
            timeout=15,
        )
        fawry_code = pay_resp.json().get("data", {}).get("bill_reference", "Error")
        return jsonify({"success": True, "payment_token": fawry_code})

    @app.route("/payments/callback")
    def payment_callback():
        success = request.args.get("success")
        merchant_order_id = request.args.get("merchant_order_id", "")

        payment_id = None
        if merchant_order_id.startswith("EDY_"):
            parts = merchant_order_id.split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                payment_id = int(parts[1])

        if success == "true" and payment_id:
            try:
                db = get_db()
                cursor = db.cursor()
                cursor.execute(
                    "UPDATE payments SET payment_status='paid', payment_date=CURDATE() WHERE id=%s",
                    (payment_id,),
                )
                db.commit()
                db.close()
                log_audit("PAYMOB_CALLBACK_PAID", "payment", payment_id, f"Order {merchant_order_id} paid")
                return render_template("success.html", message="Payment successful! Your fees have been updated.")
            except Exception as e:
                print(f"Callback DB Error: {e}")
                return render_template("success.html", message="Payment detected, but database update failed. Please contact support.")

        return render_template("error.html", message="Payment failed or was cancelled.")
