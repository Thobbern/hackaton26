from bs4 import BeautifulSoup, NavigableString, Tag
import markdownify
import markdown
import re
from urllib.parse import quote


_ADMONITION_MACROS = {"info", "warning", "note", "tip"}


def _preprocess_confluence_macros(soup: BeautifulSoup) -> None:
    for macro in soup.find_all("ac:structured-macro"):
        name = macro.get("ac:name", "")

        if name == "code":
            lang_tag = macro.find("ac:parameter", {"ac:name": "language"})
            lang = lang_tag.get_text(strip=True) if lang_tag else ""
            body_tag = macro.find("ac:plain-text-body")
            code = body_tag.get_text() if body_tag else ""
            fence = f"```{lang}\n{code}\n```"
            div = soup.new_tag("div")
            div.append(NavigableString(fence))
            macro.replace_with(div)

        elif name in _ADMONITION_MACROS:
            label = name.capitalize()
            body_tag = macro.find("ac:rich-text-body") or macro.find("ac:plain-text-body")
            inner = body_tag.decode_contents() if body_tag else ""
            inner_text = BeautifulSoup(inner, "html.parser").get_text(separator=" ").strip()
            blockquote = soup.new_tag("blockquote")
            p = soup.new_tag("p")
            strong = soup.new_tag("strong")
            strong.append(NavigableString(f"{label}:"))
            p.append(strong)
            p.append(NavigableString(f" {inner_text}"))
            blockquote.append(p)
            macro.replace_with(blockquote)

        else:
            inner = macro.decode_contents()
            placeholder = f'<!-- confluence:macro name="{name}" -->{inner}<!-- /confluence:macro -->'
            macro.replace_with(NavigableString(placeholder))


def _preprocess_confluence_links(soup: BeautifulSoup) -> None:
    for link in soup.find_all("ac:link"):
        page_tag = link.find("ri:page")
        if page_tag:
            title = page_tag.get("ri:content-title", "")
            link_body = link.find("ac:plain-text-link-body") or link.find("ac:link-body")
            link_text = link_body.get_text(strip=True) if link_body else title
            encoded_title = quote(title, safe="")
            a = soup.new_tag("a", href=encoded_title)
            a.string = link_text or title
            link.replace_with(a)
        else:
            link.unwrap()


def storage_to_markdown(xhtml: str) -> str:
    soup = BeautifulSoup(xhtml, "html.parser")

    _preprocess_confluence_macros(soup)
    _preprocess_confluence_links(soup)

    html = str(soup)
    md = markdownify.markdownify(html, heading_style="ATX", bullets="-")

    md = re.sub(r"\n{3,}", "\n\n", md)
    md = md.strip()
    return md


def _postprocess_code_blocks(html: str) -> str:
    def replace_code(m: re.Match) -> str:
        classes = m.group(1) or ""
        code = m.group(2)
        lang_match = re.search(r"language-(\w+)", classes)
        lang = lang_match.group(1) if lang_match else ""
        cdata = f"<![CDATA[{code}]]>"
        return (
            f'<ac:structured-macro ac:name="code">'
            f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
            f"<ac:plain-text-body>{cdata}</ac:plain-text-body>"
            f"</ac:structured-macro>"
        )

    return re.sub(
        r'<pre><code(?:\s+class="([^"]*)")?>(.*?)</code></pre>',
        replace_code,
        html,
        flags=re.DOTALL,
    )


_ADMONITION_LABELS = {"info", "warning", "note", "tip"}


def _postprocess_admonitions(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for blockquote in soup.find_all("blockquote"):
        paragraphs = blockquote.find_all("p")
        replacements = []
        for p in paragraphs:
            strong = p.find("strong")
            if strong:
                label_text = strong.get_text(strip=True).rstrip(":")
                if label_text.lower() in _ADMONITION_LABELS:
                    strong.extract()
                    content = p.get_text(strip=True).lstrip(": ").strip()
                    macro_tag = soup.new_tag("ac:structured-macro", **{"ac:name": label_text.lower()})
                    body_tag = soup.new_tag("ac:rich-text-body")
                    content_p = soup.new_tag("p")
                    content_p.append(NavigableString(content))
                    body_tag.append(content_p)
                    macro_tag.append(body_tag)
                    replacements.append((p, macro_tag))

        if replacements and len(replacements) == len(paragraphs):
            for p, macro_tag in replacements:
                p.replace_with(macro_tag)
            blockquote.unwrap()
        elif replacements:
            for p, macro_tag in replacements:
                p.replace_with(macro_tag)

    return str(soup)


def _postprocess_macro_comments(html: str) -> str:
    return re.sub(
        r'<!-- confluence:macro name="([^"]+)" -->(.*?)<!-- /confluence:macro -->',
        lambda m: f'<ac:structured-macro ac:name="{m.group(1)}">{m.group(2)}</ac:structured-macro>',
        html,
        flags=re.DOTALL,
    )


def markdown_to_storage(md: str) -> str:
    html = markdown.markdown(
        md,
        extensions=["fenced_code", "tables", "nl2br"],
    )

    html = _postprocess_code_blocks(html)
    html = _postprocess_admonitions(html)
    html = _postprocess_macro_comments(html)

    return html
