"""Map admin forms and paper submissions onto published record fields."""

import re
from datetime import date
from urllib.parse import unquote, urlsplit


class FormError(ValueError):
    status = 400


ARTICLE_TEXT = {"title": ("论文标题", 600), "venue": ("期刊或会议", 400),
                "abstract": ("英文摘要", 20000), "abstract_zh": ("中文摘要", 20000)}
PERSON_TEXT = {"name": ("姓名", 220), "name_zh": ("中文姓名", 220), "category": ("人员分类", 140),
               "email": ("邮箱", 320), "bio": ("英文简介", 20000), "bio_zh": ("中文简介", 20000)}
NEWS_TEXT = {"title": ("英文标题", 220), "title_zh": ("中文标题", 220), "summary": ("英文摘要", 700),
             "summary_zh": ("中文摘要", 700), "content": ("英文正文", 20000), "content_zh": ("中文正文", 20000)}
ARTICLE_URLS = {"paper_url": "论文链接", "official_free_access_url": "免费论文链接", "resource_url": "资源链接"}
ARTICLE_FLAGS = ("featured_on_home", "home_top_pinned", "is_starred")
NEWS_FLAGS = ("pinned", "hide_from_home")
RESOURCE_KINDS = frozenset({"Supplementary Material", "Code", "Dataset"})
DOI = re.compile(r"10\.[0-9]{4,9}/[^\s?#]+", re.IGNORECASE)
PAPER_FIELDS = set(ARTICLE_TEXT) | set(ARTICLE_URLS) | set(ARTICLE_FLAGS) | {
    "authors", "year", "doi", "show_abstract_zh", "authors_display_count", "resource_kinds",
}


def _text(value, label, limit=2000):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FormError(f"{label}须为文本。")
    value = value.strip()
    if len(value) > limit:
        raise FormError(f"{label}最多可填写 {limit} 个字符。")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise FormError(f"{label}包含无效字符。") from None
    if "\x00" in value:
        raise FormError(f"{label}包含无效字符。")
    return value


def _text_fields(values, current, fields):
    return {key: _text(values.get(key, current.get(key)), label, limit) for key, (label, limit) in fields.items()}


def validate_link(value, label, *, allow_mailto=False):
    value = _text(value, label)
    if not value:
        return ""
    schemes = "HTTP、HTTPS 或 mailto" if allow_mailto else "HTTP 或 HTTPS"
    message = f"{label}须为有效的 {schemes} 链接。"
    if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        raise FormError(message)
    try:
        parts = urlsplit(value)
        if parts.scheme in ("http", "https") and parts.hostname and parts.username is None:
            parts.port
            return value
        if allow_mailto and parts.scheme == "mailto" and not parts.netloc and "@" in parts.path:
            return value
    except ValueError:
        pass
    raise FormError(message)


def _integer(value, label, *, minimum, maximum, empty=None):
    if value is None or value == "":
        return empty
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,4}", value.strip()):
        number = int(value)
    else:
        raise FormError(f"{label}须为整数。")
    if not minimum <= number <= maximum:
        raise FormError(f"{label}须在 {minimum} 到 {maximum} 之间。")
    return number


def _authors(values):
    if not isinstance(values, list) or len(values) > 250:
        raise FormError("作者须以列表提交，最多 250 位。")
    return [name for value in values if (name := _text(value, "作者姓名", 220))]


def _resource_kinds(values):
    if not isinstance(values, list) or any(not isinstance(value, str) or value not in RESOURCE_KINDS for value in values):
        raise FormError("论文资源类型须从补充材料、代码和数据集中选择。")
    return list(dict.fromkeys(values))


def _article_fields(values, current, authors):
    return {
        **_text_fields(values, current, ARTICLE_TEXT),
        **{key: validate_link(values.get(key, current.get(key)), label) for key, label in ARTICLE_URLS.items()},
        "authors": _authors(authors),
        "year": _integer(values.get("year", current.get("year")), "年份", minimum=1000, maximum=9999, empty=""),
        "authors_display_count": _integer(values.get("authors_display_count", current.get("authors_display_count")),
                                          "作者展示人数", minimum=1, maximum=9999, empty=3),
    }


def _article_form(form, record, resolve_article_flags):
    authors = form["authors"].split(",") if "authors" in form else record.get("authors", [])
    article = _article_fields(form, record, authors)
    if not article["title"]:
        raise FormError("请填写论文标题后保存草稿。")
    flag_inputs = dict(form.items())
    flag_inputs["official_free_access_url"] = article["official_free_access_url"]
    article.update(zip(ARTICLE_FLAGS, resolve_article_flags(flag_inputs)))
    article["show_abstract_zh"] = form.get("show_abstract_zh") == "on"
    kinds = form.getlist("resource_kinds")
    article["resource_kinds"] = _resource_kinds(kinds) if kinds else record.get("resource_kinds", ["Code"])
    return article


def _person_form(form, record, normalize_person_tags):
    person = _text_fields(form, record, PERSON_TEXT)
    person["name"] = person["name"] or person["name_zh"]
    if not person["name"]:
        raise FormError("请至少填写一种语言的姓名后保存草稿。")
    links = []
    for index in range(1, 4):
        label = _text(form.get(f"link_name_{index}"), "个人链接名称", 220)
        url = validate_link(form.get(f"link_url_{index}"), "个人链接", allow_mailto=True)
        if label and url:
            links.append({"name": label, "url": url})
    person["links"] = links if any(key.startswith(("link_name_", "link_url_")) for key in form) else record.get("links", [])
    tags = [_text(tag, "人员标签", 100) for tag in form.getlist("person_tags")]
    person["tags"] = normalize_person_tags(tags)
    return person


def _news_date(value):
    value = _text(value, "日期", 10)
    if not value:
        return ""
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise FormError("新闻日期须采用 YYYY-MM-DD 格式，并且是有效日期。")


def _news_form(form, record):
    news = _text_fields(form, record, NEWS_TEXT)
    if not (news["title"] or news["title_zh"]):
        raise FormError("请至少填写一种语言的新闻标题后保存草稿。")
    news["date"] = _news_date(form.get("date", record.get("date")))
    news.update({key: form.get(key) == "on" for key in NEWS_FLAGS})
    return news


def from_form(kind, form, current=None, *, resolve_article_flags, normalize_person_tags):
    """Apply a legacy admin form while retaining stored fields it cannot edit."""
    record = dict(current or {})
    if kind == "article":
        record.update(_article_form(form, record, resolve_article_flags))
    elif kind == "person":
        record.update(_person_form(form, record, normalize_person_tags))
    elif kind == "news":
        record.update(_news_form(form, record))
    else:
        raise FormError("不支持的内容类型。")
    return record


def _normalized_doi(value):
    if value.casefold().startswith("doi:"):
        value = value[4:].strip()
    prefix = re.match(r"https?://(?:dx\.)?doi\.org/", value, re.IGNORECASE)
    if prefix:
        value = unquote(urlsplit(value).path.lstrip("/"))
    return value.lower() if DOI.fullmatch(value) and all(ord(character) >= 32 for character in value) else ""


def paper_doi(record):
    """Recognize a DOI also in links stored by the legacy publication form."""
    value = record.get("doi") or ""
    if isinstance(value, str) and (doi := _normalized_doi(value.strip())):
        return doi
    for key in ("paper_url", "official_free_access_url"):
        link = record.get(key)
        if isinstance(link, str) and (match := DOI.search(unquote(link))):
            return match.group().lower()
    return ""


def from_paper_payload(payload, current=None):
    """Apply JSON paper metadata without resetting legacy display choices."""
    if not isinstance(payload, dict) or payload.keys() - PAPER_FIELDS:
        raise FormError("论文草稿包含不支持的字段。")
    record = dict(current or {})
    record.update(_article_fields(payload, record, payload.get("authors", record.get("authors", []))))
    if not record["title"]:
        raise FormError("请填写论文标题后保存草稿。")
    if "doi" in payload:
        supplied = _text(payload["doi"], "DOI", 250)
        record["doi"] = _normalized_doi(supplied)
        if supplied and not record["doi"]:
            raise FormError("DOI 格式无效。")
    for key in (*ARTICLE_FLAGS, "show_abstract_zh"):
        if key in payload and type(payload[key]) is not bool:
            raise FormError("论文展示选项须为布尔值。")
        record[key] = payload.get(key, bool(record.get(key)))
    if record["home_top_pinned"]:
        record["featured_on_home"] = True
    record["resource_kinds"] = (_resource_kinds(payload["resource_kinds"]) if "resource_kinds" in payload
                                else record.get("resource_kinds", ["Code"]))
    return record


def validate_for_publish(kind, record):
    """Reject incomplete drafts when publishing or restoring public content."""
    if kind == "article":
        missing = [label for key, label in (("title", "论文标题"), ("authors", "作者"), ("venue", "期刊或会议"),
                                            ("year", "年份"), ("abstract", "英文摘要")) if not record.get(key)]
        if missing:
            raise FormError(f"发布前请填写{'、'.join(missing)}。")
        _integer(record["year"], "年份", minimum=1000, maximum=9999)
    elif kind == "person":
        if not (record.get("name") or record.get("name_zh")) or not record.get("category"):
            raise FormError("发布前请填写人员姓名和分类。")
    elif kind == "news":
        has_content = any(record.get(f"title{suffix}") and record.get(f"content{suffix}") for suffix in ("", "_zh"))
        if not has_content or not _news_date(record.get("date")):
            raise FormError("发布前请填写日期，以及至少一组相同语言的新闻标题和正文。")
    else:
        raise FormError("不支持的内容类型。")
