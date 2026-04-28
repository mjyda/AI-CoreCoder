from __future__ import annotations

import json
import re


class MailTaskMixin:
    def _run_mail_task(self, user_input: str) -> str:
        expert = self._experts["mail"]
        tool_names = [tool.name for tool in expert.tools]
        if not {"gmail_list_recent", "gmail_search", "gmail_get_content"}.intersection(tool_names):
            return self._format_evidence_result(
                summary="当前无法执行真实邮件检索：尚未接入 Gmail MCP 工具。",
                route_key="mail",
                evidence_lines=[f"available_tools={tool_names}", "required_tools=['gmail_list_recent','gmail_search','gmail_get_content']"],
                detail="请先接入 gmail-mcp，再执行邮件类任务。",
            )

        def _tool(name: str):
            return next((t for t in expert.tools if t.name == name), None)

        text = user_input.strip()
        lower = text.lower()
        mail_intent = self._parse_mail_intent(user_input)
        m_uid = re.search(r"(?:uid|邮件id|邮件编号)\s*[:：=]?\s*([A-Za-z0-9]+)", text, flags=re.IGNORECASE)
        uid_value = m_uid.group(1) if m_uid else ""

        if self._pending_mail_action is not None:
            if self._is_mail_confirm(text):
                pending = self._pending_mail_action
                self._pending_mail_action = None
                act = str(pending.get("action", ""))
                if act == "send":
                    tool = _tool("gmail_send_email")
                    if tool is None:
                        return "Error: gmail_send_email is not available."
                    result = tool.execute(to=str(pending.get("to", "")), subject=str(pending.get("subject", "")), body=str(pending.get("body", "")))
                    return self._format_evidence_result("已按确认发送邮件。", "mail", [f"tool=gmail_send_email args={pending}"], result)
                if act == "reply":
                    tool = _tool("gmail_reply_email")
                    if tool is None:
                        return "Error: gmail_reply_email is not available."
                    result = tool.execute(uid=str(pending.get("uid", "")), body=str(pending.get("body", "")))
                    return self._format_evidence_result("已按确认回复邮件。", "mail", [f"tool=gmail_reply_email args={pending}"], result)
                if act in ("delete", "trash"):
                    tname = "gmail_delete_email" if act == "delete" else "gmail_move_to_trash"
                    tool = _tool(tname)
                    if tool is None:
                        return f"Error: {tname} is not available."
                    result = tool.execute(uid=str(pending.get("uid", "")))
                    return self._format_evidence_result("已按确认执行邮件删除动作。", "mail", [f"tool={tname} args={pending}"], result)
            elif any(k in text for k in ("取消", "不用", "算了", "否", "no")):
                self._pending_mail_action = None
                return self._format_evidence_result("已取消待确认的邮件操作。", "mail", ["pending_action=cleared"], "未执行发送/回复/删除。")

        uid_read, uid_clarify = self._should_get_mail_by_uid(text, mail_intent, bool(uid_value))
        if uid_clarify:
            return self._format_evidence_result("检测到你可能要读取指定邮件，但 UID 意图不够确定。", "mail", [f"mail_intent={mail_intent}", f"uid_candidate={uid_value!r}"], "请确认：A) 按 UID 读取完整邮件；B) 搜索邮件；C) 仅列出最近邮件。")
        if uid_read:
            tool = _tool("gmail_get_content")
            if tool is None:
                return "Error: gmail_get_content is not available."
            result = tool.execute(uid=uid_value)
            return self._format_evidence_result("已获取指定邮件内容。", "mail", [f"tool=gmail_get_content args={{'uid': {uid_value!r}}}"], result)

        should_search, search_clarify = self._should_search_mail(text, mail_intent)
        if search_clarify:
            return self._format_evidence_result("检测到你可能要搜索邮件，但搜索意图不够明确。", "mail", [f"mail_intent={mail_intent}"], "请确认：A) 搜索邮件（并给关键词）；B) 读取指定 UID；C) 列最近邮件。")
        if should_search:
            tool = _tool("gmail_search")
            if tool is None:
                return "Error: gmail_search is not available."
            m = re.search(r"(?:搜索|查找|包含)\s*[:：]?\s*(.+)$", text)
            q = (m.group(1).strip() if m else "") or lower.replace("邮件", "").replace("搜索", "").replace("查找", "").strip()
            limit = self._extract_result_limit(text, default=10)
            result = tool.execute(query=q, limit=limit)
            return self._format_evidence_result("已完成邮件搜索。", "mail", [f"tool=gmail_search args={{'query': {q!r}, 'limit': {limit}}}"], result)

        send_signal = any(k in text for k in ("发邮件", "发送邮件", "发送给"))
        reply_signal = any(k in text for k in ("回复邮件", "回复这封", "回信"))
        delete_signal = any(k in text for k in ("删除邮件", "删除这封", "删掉这封"))
        mark_signal = any(k in text for k in ("标记已读", "设为已读"))
        trash_signal = any(k in text for k in ("移到垃圾箱", "移至垃圾箱", "扔到垃圾箱"))

        if mail_intent.get("action") == "send" or send_signal:
            to = self._extract_email_address(text)
            subject, body = self._parse_mail_subject_body(text)
            subject = subject or "(无主题)"
            if not to or not body:
                return self._format_evidence_result("检测到发送邮件意图，但收件人或正文不完整。", "mail", [f"mail_intent={mail_intent}"], "请补充：收件人邮箱、主题、正文。示例：发邮件给 a@b.com，主题 xx，正文 yy")
            self._pending_mail_action = {"action": "send", "to": to, "subject": subject, "body": body}
            return self._format_evidence_result("已生成发信草稿，请确认后发送。", "mail", [f"draft={self._pending_mail_action}"], f"收件人：{to}\n主题：{subject}\n正文摘要：{body[:200]}\n\n回复“确认发送”以执行，或“取消”。")

        if mail_intent.get("action") == "reply" or reply_signal:
            if not uid_value:
                return self._format_evidence_result("检测到回复意图，但缺少 UID。", "mail", [f"mail_intent={mail_intent}"], "请提供 uid，例如：回复 uid=123，正文 ...")
            body = self._extract_after_keyword(text, ("正文", "内容", "回复", "body")) or ""
            if not body:
                return self._format_evidence_result("检测到回复意图，但缺少回复正文。", "mail", [f"uid={uid_value!r}"], "请补充：回复 uid=123，正文 你好，已收到。")
            self._pending_mail_action = {"action": "reply", "uid": uid_value, "body": body}
            return self._format_evidence_result("已生成回复草稿，请确认后发送。", "mail", [f"draft={self._pending_mail_action}"], f"UID：{uid_value}\n回复摘要：{body[:200]}\n\n回复“确认发送”以执行，或“取消”。")

        if delete_signal or trash_signal or mail_intent.get("action") in ("delete", "move_to_trash"):
            if not uid_value:
                return self._format_evidence_result("检测到删除/移动垃圾箱意图，但缺少 UID。", "mail", [f"mail_intent={mail_intent}"], "请提供 uid，例如：删除 uid=123。")
            act = "trash" if (trash_signal or mail_intent.get("action") == "move_to_trash") else "delete"
            self._pending_mail_action = {"action": act, "uid": uid_value}
            return self._format_evidence_result("删除类操作需要二次确认。", "mail", [f"pending={self._pending_mail_action}"], f"将对 UID={uid_value} 执行{'移到垃圾箱' if act == 'trash' else '删除并清理'}。\n回复“确认删除”执行，或“取消”。")

        if mark_signal or mail_intent.get("action") == "mark_read":
            if not uid_value:
                return self._format_evidence_result("检测到标记已读意图，但缺少 UID。", "mail", [f"mail_intent={mail_intent}"], "请提供 uid，例如：标记 uid=123 为已读。")
            mtool = _tool("gmail_mark_read")
            if mtool is None:
                return "Error: gmail_mark_read is not available."
            result = mtool.execute(uid=uid_value)
            return self._format_evidence_result("已执行标记已读。", "mail", [f"tool=gmail_mark_read args={{'uid': {uid_value!r}}}"], result)

        tool = _tool("gmail_list_recent")
        if tool is None:
            return "Error: gmail_list_recent is not available."
        limit = self._extract_result_limit(text, default=5)
        summarize_each, need_clarify = self._should_summarize_each_mail(text, mail_intent)
        if need_clarify:
            return self._format_evidence_result("检测到你可能希望逐封概括邮件，但意图不够明确，先确认一次。", "mail", [f"mail_intent={mail_intent}"], "请确认：\nA) 仅列出最近邮件\nB) 逐封概括最近邮件\n也可以直接说：'逐封概括最近5封邮件'。")
        result = tool.execute(limit=limit)
        if result.startswith("Error: IMAP env is incomplete"):
            return self._format_evidence_result("邮件助手已就绪，但缺少 IMAP 配置。", "mail", [f"tool=gmail_list_recent args={{'limit': {limit}}}"], "请先设置环境变量后重试：\nCORECODER_IMAP_HOST, CORECODER_IMAP_PORT, CORECODER_EMAIL_ADDRESS, CORECODER_EMAIL_APP_PASSWORD")
        if summarize_each:
            get_tool = _tool("gmail_get_content")
            if get_tool is None:
                return self._format_evidence_result("无法逐封概括：缺少 gmail_get_content 工具。", "mail", [f"tool=gmail_list_recent args={{'limit': {limit}}}"], result)
            uids = self._extract_mail_uids(result)
            if not uids:
                return self._format_evidence_result("未解析到可概括的邮件 UID。", "mail", [f"tool=gmail_list_recent args={{'limit': {limit}}}"], result)
            summaries: list[str] = []
            evidence = [f"tool=gmail_list_recent args={{'limit': {limit}}}"]
            for uid in uids[:limit]:
                raw_mail = get_tool.execute(uid=uid, max_chars=6000)
                evidence.append(f"tool=gmail_get_content args={{'uid': {uid!r}, 'max_chars': 6000}}")
                if raw_mail.startswith("Error:"):
                    summaries.append(f"- uid={uid}: 读取失败：{raw_mail}")
                    continue
                summaries.append(f"- uid={uid}: {self._summarize_single_email(raw_mail)}")
            return self._format_evidence_result(f"已逐封概括最近 {min(limit, len(uids))} 封邮件。", "mail", evidence, "\n".join(summaries))
        return self._format_evidence_result(f"已获取最近 {limit} 封邮件。", "mail", [f"tool=gmail_list_recent args={{'limit': {limit}}}"], result)

    @staticmethod
    def _should_get_mail_by_uid(text: str, intent: dict, has_uid: bool) -> tuple[bool, bool]:
        llm_action = str(intent.get("action", "unknown"))
        llm_conf = float(intent.get("confidence", 0.0) or 0.0)
        llm_needs = bool(intent.get("needs_clarification", False))
        rule_positive = has_uid or any(k in text for k in ("读取这封", "查看这封", "打开这封"))
        if llm_action == "get_by_uid" and llm_conf >= 0.72 and rule_positive and not llm_needs:
            return True, False
        if rule_positive and llm_action in ("unknown", "search") and llm_conf < 0.7:
            return False, True
        return False, False

    @staticmethod
    def _should_search_mail(text: str, intent: dict) -> tuple[bool, bool]:
        llm_action = str(intent.get("action", "unknown"))
        llm_conf = float(intent.get("confidence", 0.0) or 0.0)
        llm_needs = bool(intent.get("needs_clarification", False))
        rule_positive = any(k in text for k in ("搜索", "查找", "包含", "keyword", "query"))
        rule_negative = any(k in text for k in ("不要搜索", "不用搜索", "仅列出", "只看最近"))
        if rule_negative:
            return False, False
        if llm_action == "search" and llm_conf >= 0.72 and rule_positive and not llm_needs:
            return True, False
        if rule_positive and llm_action in ("unknown", "list") and llm_conf < 0.7:
            return False, True
        return False, False

    def _parse_mail_intent(self, user_input: str) -> dict:
        schema = {"action": "list", "limit": 5, "confidence": 0.0, "needs_clarification": False, "reason": ""}
        prompt = (
            "你是邮件任务意图解析器。只输出JSON。\n"
            "字段：action(list/summarize_each/get_by_uid/search/send/reply/delete/mark_read/move_to_trash/unknown), "
            "limit, confidence(0~1), needs_clarification(bool), reason。\n"
            "规则：\n1) 若用户想逐封/逐条概括每封邮件，action=summarize_each。\n2) 仅查看最近邮件列表，action=list。\n"
            "3) 有搜索词时 action=search；指定uid时 action=get_by_uid。\n"
            "4) 发送新邮件 action=send；回复 action=reply；删除 action=delete；设已读 action=mark_read；移到垃圾箱 action=move_to_trash。\n"
            "4) 不确定时 action=unknown 且 needs_clarification=true。\n"
            f"默认值: {json.dumps(schema, ensure_ascii=False)}\n用户输入: {user_input}"
        )
        parsed = self._parse_intent_json(prompt, schema)
        action = str(parsed.get("action", "unknown")).strip().lower()
        allowed = {"list", "summarize_each", "get_by_uid", "search", "send", "reply", "delete", "mark_read", "move_to_trash", "unknown"}
        if action not in allowed:
            action = "unknown"
        conf = max(0.0, min(float(parsed.get("confidence", 0.0) or 0.0), 1.0))
        limit = max(1, min(int(parsed.get("limit", 5) or 5), 50))
        return {"action": action, "limit": limit, "confidence": conf, "needs_clarification": bool(parsed.get("needs_clarification", False)), "reason": str(parsed.get("reason", "")).strip()}

    @staticmethod
    def _extract_email_address(text: str) -> str:
        m = re.search(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", text)
        return m.group(1) if m else ""

    @staticmethod
    def _is_mail_confirm(text: str) -> bool:
        return any(k in text for k in ("确认发送", "确认删除", "确认执行", "确认", "yes", "ok"))

    @staticmethod
    def _parse_mail_subject_body(text: str) -> tuple[str, str]:
        raw = (text or "").strip()
        m = re.search(r"(?:主题|subject)\s*[:：]?\s*(.*?)\s*(?:，|,|;|；|\s)+(?:正文|内容|body)\s*[:：]?\s*(.+)$", raw, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip(" ，,;；"), m.group(2).strip()
        subject = MailTaskMixin._extract_after_keyword(raw, ("主题", "subject")).strip()
        body = MailTaskMixin._extract_after_keyword(raw, ("正文", "内容", "body")).strip()
        if subject:
            subject = re.split(r"(?:，|,|;|；|\s)+(?:正文|内容|body)\b", subject, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ，,;；")
        return subject, body

    @staticmethod
    def _should_summarize_each_mail(text: str, intent: dict) -> tuple[bool, bool]:
        llm_action = str(intent.get("action", "unknown"))
        llm_conf = float(intent.get("confidence", 0.0) or 0.0)
        llm_needs = bool(intent.get("needs_clarification", False))
        rule_positive = any(k in text for k in ("概括", "总结", "每一封", "逐封", "逐条", "提炼"))
        rule_negative = any(k in text for k in ("仅列出", "只列出", "不要总结", "不需要总结"))
        if rule_negative:
            return False, False
        llm_yes = llm_action == "summarize_each" and llm_conf >= 0.72 and not llm_needs
        if llm_yes and rule_positive:
            return True, False
        if llm_yes and not rule_positive:
            return False, True
        if rule_positive and llm_action in ("unknown", "list") and llm_conf < 0.7:
            return False, True
        return False, False

    @staticmethod
    def _extract_mail_uids(raw_list: str) -> list[str]:
        return [m.group(1) for line in raw_list.splitlines() if (m := re.search(r"\buid=([A-Za-z0-9]+)\b", line))]

    def _summarize_single_email(self, raw_mail: str) -> str:
        prompt = "请基于以下邮件内容，输出一句中文摘要（20-50字），说明邮件主题与需要我做什么；不要编造。\n\n" + raw_mail[:7000]
        try:
            resp = self.llm.chat(messages=[{"role": "system", "content": "你是邮件摘要助手，只输出简洁中文摘要一句。"}, {"role": "user", "content": prompt}])
            text = re.sub(r"\s+", " ", (resp.content or "").strip())
            return text[:200] if text else "（摘要为空）"
        except Exception:
            subject = ""
            from_ = ""
            for ln in raw_mail.splitlines():
                if ln.lower().startswith("subject:"):
                    subject = ln.split(":", 1)[1].strip()
                elif ln.lower().startswith("from:"):
                    from_ = ln.split(":", 1)[1].strip()
                if subject and from_:
                    break
            if subject or from_:
                return f"来自{from_ or '未知发件人'}，主题“{subject or '（无主题）'}”。"
            return "（邮件摘要失败）"

    @staticmethod
    def _extract_after_keyword(text: str, keywords: tuple[str, ...]) -> str:
        for kw in keywords:
            idx = text.find(kw)
            if idx >= 0:
                return text[idx + len(kw):].strip(" ：:，,。")
        return ""
