"""Code generators for Java, C#, Go, PHP, Ruby and Node (axios). Each takes the resolved request pieces."""
import json


def _dq(s):
    """Double-quoted string literal valid in Java, C# and Go."""
    return json.dumps(s, ensure_ascii=False)


def _sq_php(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sq_ruby(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def java(method, url, headers, text):
    lines = ["import java.net.URI;", "import java.net.http.HttpClient;", "import java.net.http.HttpRequest;",
             "import java.net.http.HttpResponse;", "", "public class Main {",
             "    public static void main(String[] args) throws Exception {",
             "        HttpClient client = HttpClient.newHttpClient();"]
    publisher = f"HttpRequest.BodyPublishers.ofString({_dq(text)})" if text is not None \
        else "HttpRequest.BodyPublishers.noBody()"
    lines.append("        HttpRequest request = HttpRequest.newBuilder()")
    lines.append(f"            .uri(URI.create({_dq(url)}))")
    for k, v in headers:
        lines.append(f"            .header({_dq(k)}, {_dq(v)})")
    lines.append(f"            .method({_dq(method)}, {publisher})")
    lines.append("            .build();")
    lines += ["        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());",
              "        System.out.println(response.statusCode());", "        System.out.println(response.body());",
              "    }", "}"]
    return "\n".join(lines)


def csharp(method, url, headers, text):
    lines = ["using System;", "using System.Net.Http;", "using System.Text;", "", "var client = new HttpClient();",
             f"var request = new HttpRequestMessage(new HttpMethod({_dq(method)}), {_dq(url)});"]
    content_type = None
    for k, v in headers:
        if k.lower() == "content-type":
            content_type = v
        else:
            lines.append(f"request.Headers.TryAddWithoutValidation({_dq(k)}, {_dq(v)});")
    if text is not None:
        ct = content_type or "text/plain"
        lines.append(f"request.Content = new StringContent({_dq(text)}, Encoding.UTF8, {_dq(ct.split(';')[0])});")
    lines += ["var response = await client.SendAsync(request);", "Console.WriteLine((int)response.StatusCode);",
              "Console.WriteLine(await response.Content.ReadAsStringAsync());"]
    return "\n".join(lines)


def go(method, url, headers, text):
    imports = ['"fmt"', '"io"', '"net/http"'] + (['"strings"'] if text is not None else [])
    body = f"strings.NewReader({_dq(text)})" if text is not None else "nil"
    lines = ["package main", "", "import (" + "".join(f"\n\t{i}" for i in imports) + "\n)", "",
             "func main() {",
             f"\treq, err := http.NewRequest({_dq(method)}, {_dq(url)}, {body})",
             "\tif err != nil {", "\t\tpanic(err)", "\t}"]
    for k, v in headers:
        lines.append(f"\treq.Header.Set({_dq(k)}, {_dq(v)})")
    lines += ["\tres, err := http.DefaultClient.Do(req)", "\tif err != nil {", "\t\tpanic(err)", "\t}",
              "\tdefer res.Body.Close()", "\tdata, _ := io.ReadAll(res.Body)",
              "\tfmt.Println(res.StatusCode)", "\tfmt.Println(string(data))", "}"]
    return "\n".join(lines)


def php(method, url, headers, text):
    lines = ["<?php", "$ch = curl_init();", "curl_setopt_array($ch, [",
             f"    CURLOPT_URL => {_sq_php(url)},", "    CURLOPT_RETURNTRANSFER => true,",
             f"    CURLOPT_CUSTOMREQUEST => {_sq_php(method)},"]
    if headers:
        lines.append("    CURLOPT_HTTPHEADER => [")
        lines += [f"        {_sq_php(f'{k}: {v}')}," for k, v in headers]
        lines.append("    ],")
    if text is not None:
        lines.append(f"    CURLOPT_POSTFIELDS => {_sq_php(text)},")
    lines += ["]);", "$response = curl_exec($ch);", "echo curl_getinfo($ch, CURLINFO_HTTP_CODE), PHP_EOL, $response;",
              "curl_close($ch);"]
    return "\n".join(lines)


def ruby(method, url, headers, text):
    cls = {"GET": "Get", "POST": "Post", "PUT": "Put", "PATCH": "Patch", "DELETE": "Delete", "HEAD": "Head",
           "OPTIONS": "Options"}.get(method, "Get")
    lines = ["require 'net/http'", "require 'uri'", "", f"uri = URI({_sq_ruby(url)})",
             f"request = Net::HTTP::{cls}.new(uri)"]
    lines += [f"request[{_sq_ruby(k)}] = {_sq_ruby(v)}" for k, v in headers]
    if text is not None:
        lines.append(f"request.body = {_sq_ruby(text)}")
    lines += ["response = Net::HTTP.start(uri.hostname, uri.port, use_ssl: uri.scheme == 'https') { |http| http.request(request) }",
              "puts response.code", "puts response.body"]
    return "\n".join(lines)


def node_axios(method, url, headers, text):
    cfg = [f"  method: {_dq(method.lower())}", f"  url: {_dq(url)}"]
    if headers:
        cfg.append("  headers: " + json.dumps(dict(headers), indent=4).replace("\n", "\n  "))
    if text is not None:
        cfg.append(f"  data: {_dq(text)}")
    return ("const axios = require('axios');\n\naxios({\n" + ",\n".join(cfg) +
            "\n}).then(r => {\n  console.log(r.status);\n  console.log(r.data);\n});")


GENERATORS = {"Java (HttpClient)": java, "C# (HttpClient)": csharp, "Go (net/http)": go, "PHP (cURL)": php,
              "Ruby (Net::HTTP)": ruby, "Node.js (axios)": node_axios}
