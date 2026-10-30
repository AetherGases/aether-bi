"""Gera o relatorio Aether a partir de dados/relatorio.json. Usa apenas Python padrao."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import html
import json
import os
from pathlib import Path
import tempfile

TOLERANCE = Decimal('0.000001')
METRICS = ('emitted_tco2e', 'reduced_tco2e', 'balance_tco2e')
TABLES = ('dim_org_hierarchy', 'fato_emissoes', 'fato_reducoes', 'kpis_inventario', 'kpis_anuais', 'qualidade_dados')
QUALITY_FIELDS = ('category_uninformed_count', 'missing_gas_count', 'missing_scope_count', 'missing_enterprise_count')


def esc(value):
    return html.escape(str(value), quote=True)


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError(f'Valor numerico invalido: {value!r}')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f'Valor numerico invalido: {value!r}') from None
    if not result.is_finite():
        raise ValueError('Numero nao finito no JSON.')
    return result


def count(value):
    result = number(value)
    if result < 0 or result != result.to_integral_value():
        raise ValueError(f'Contagem invalida: {value!r}')
    return int(result)


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Texto obrigatorio ausente: {label}')
    return value


def close(actual, expected, label):
    if abs(actual - expected) > TOLERANCE:
        raise ValueError(f'Falha de conciliacao: {label}')


def rate(reduced, emitted):
    return reduced / emitted * 100 if emitted else None


def br(value, digits=3):
    return f'{number(value):,.{digits}f}'.replace(',', '@').replace('.', ',').replace('@', '.')


def fmt_pct(value, digits=4):
    return 'Não aplicável' if value is None else br(value, digits) + '%'


def period(row):
    start, end = date.fromisoformat(row['period_start']), date.fromisoformat(row['period_end'])
    if end < start:
        raise ValueError('Periodo invertido no JSON.')
    return start, end


def amounts(row):
    emitted, reduced, balance = (number(row[key]) for key in METRICS)
    if emitted < 0 or reduced < 0:
        raise ValueError('Emissoes ou reducoes negativas.')
    close(balance, emitted - reduced, 'saldo')
    expected = rate(reduced, emitted)
    if expected is None:
        if row['reduction_rate_pct'] is not None:
            raise ValueError('Taxa deve ser nula quando as emissoes sao zero.')
    else:
        close(number(row['reduction_rate_pct']), expected, 'taxa percentual')
    return emitted, reduced, balance


def reject_constant(value):
    raise ValueError(f'Constante JSON invalida: {value}')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Campo JSON duplicado: {key}')
        result[key] = value
    return result


def load_report(path):
    raw = Path(path).read_bytes()
    data = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal,
                      parse_constant=reject_constant, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('Contrato nao suportado; esperado schema_version 1.')
    for key in ('batch_id', 'source', 'scope_note'):
        text(data[key], key)
    for key in ('export_job_id', 'export_run_id'):
        if data[key] is not None:
            text(data[key], key)
    validation = data['validation']
    if validation['inventory_reconciliation'] != 'passed':
        raise ValueError('Conciliacao da exportacao nao aprovada.')
    if number(validation['absolute_tolerance_tco2e']) != TOLERANCE:
        raise ValueError('Tolerancia diferente do contrato esperado.')
    text(validation['note'], 'validation.note')
    extracted = datetime.fromisoformat(data['ingested_at_utc'].replace('Z', '+00:00'))
    if extracted.utcoffset() is None:
        raise ValueError('Data de extracao sem fuso horario.')
    summary = data['summary']
    totals = amounts(summary)
    start, end = period(summary)
    coverage = {key: count(summary[key]) for key in ('inventories', 'enterprises', 'units')}
    if not coverage['inventories']:
        raise ValueError('Carga sem inventarios.')
    if any(coverage[key] > coverage['inventories'] for key in ('enterprises', 'units')):
        raise ValueError('Cobertura organizacional excede os inventarios.')
    counts = {key: count(data['row_counts'][key]) for key in TABLES}
    if counts['kpis_inventario'] != coverage['inventories']:
        raise ValueError('Contagem de inventarios divergente dos KPIs.')
    groups = data['by_year_status_type']
    if not isinstance(groups, list) or not 1 <= len(groups) <= 1000:
        raise ValueError('Esperados entre 1 e 1000 grupos anuais.')
    annual = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    keys, statuses, types = set(), set(), set()
    starts, ends = [], []
    inventory_count = 0
    for row in groups:
        year = count(row['inventory_year'])
        status = text(row['inventory_status'], 'inventory_status')
        kind = text(row['inventory_type'], 'inventory_type')
        key = (year, status, kind)
        if key in keys:
            raise ValueError(f'Grupo anual duplicado: {key!r}')
        keys.add(key)
        group_start, group_end = period(row)
        if year != group_start.year or not start <= group_start <= group_end <= end:
            raise ValueError('Ano ou periodo do grupo inconsistente com o resumo.')
        group_count = count(row['inventories'])
        if not group_count:
            raise ValueError('Grupo anual sem inventarios.')
        for field in ('enterprises', 'units'):
            if count(row[field]) > min(group_count, coverage[field]):
                raise ValueError('Cobertura do grupo inconsistente.')
        inventory_count += group_count
        for index, value in enumerate(amounts(row)):
            annual[year][index] += value
        starts.append(group_start)
        ends.append(group_end)
        statuses.add(status)
        types.add(kind)
    if inventory_count != coverage['inventories']:
        raise ValueError('Inventarios dos grupos nao fecham com o resumo.')
    if min(starts) != start or max(ends) != end:
        raise ValueError('Limites anuais nao fecham com o periodo global.')
    for index, expected in enumerate(totals):
        close(sum((values[index] for values in annual.values()), Decimal(0)), expected, METRICS[index])
    quality = data['quality_from_emissions']
    emission_count = count(quality['emission_count'])
    if emission_count != counts['fato_emissoes']:
        raise ValueError('Contagem de emissoes divergente da qualidade.')
    quality_counts = {key: count(quality[key]) for key in QUALITY_FIELDS}
    if any(value > emission_count for value in quality_counts.values()):
        raise ValueError('Ocorrencias de qualidade excedem as emissoes.')
    return dict(data=data, totals=totals, start=start, end=end, coverage=coverage,
                counts=counts, annual=dict(annual), statuses=sorted(statuses), types=sorted(types),
                quality=quality_counts, emission_count=emission_count,
                extracted=extracted.astimezone(timezone.utc), sha256=hashlib.sha256(raw).hexdigest())


def table(headers, rows, caption=''):
    heading = ''.join(f'<th scope="col">{esc(value)}</th>' for value in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in row) + '</tr>' for row in rows)
    return f'<table><caption>{esc(caption)}</caption><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>'


def cards(items):
    return '<div class="cards">' + ''.join(
        f'<div class="card"><strong>{esc(value)}</strong><span>{esc(label)}</span><small>{esc(detail)}</small></div>'
        for value, label, detail in items) + '</div>'


def note(title, body):
    return f'<aside class="note"><strong>{esc(title)}</strong><p>{esc(body)}</p></aside>'


def bars(years, values):
    maximum = max(max((v for v in values if v is not None), default=Decimal(0)) * Decimal('1.05'), Decimal(1))
    svg = f'<svg viewBox="0 0 640 {28 + 48 * len(years)}" role="img" aria-label="Taxas anuais de redução" xmlns="http://www.w3.org/2000/svg">'
    for index, (year, value) in enumerate(zip(years, values)):
        y = 16 + index * 48
        width = value / maximum * 350 if value is not None else Decimal(0)
        svg += (f'<text x="0" y="{y + 18}" fill="#0d3b35" font-size="15">{year}</text>'
                f'<rect x="65" y="{y}" width="350" height="25" rx="5" fill="#edf3f0"/>'
                f'<rect x="65" y="{y}" width="{width:.2f}" height="25" rx="5" fill="#5b21b6"/>'
                f'<text x="435" y="{y + 18}" fill="#0d3b35" font-size="15">{esc(fmt_pct(value, 2))}</text>')
    return svg + '</svg>'


def render_report(report):
    data, totals = report['data'], report['totals']
    emitted, reduced, balance = totals
    coverage, counts, annual = report['coverage'], report['counts'], report['annual']
    years = sorted(annual)
    start, end, extracted = report['start'], report['end'], report['extracted']
    quality, emission_count = report['quality'], report['emission_count']
    missing = quality['category_uninformed_count']
    missing_pct = Decimal(missing) / emission_count * 100 if emission_count else None
    period_label = str(years[0]) if len(years) == 1 else f'{years[0]} a {years[-1]}'
    provenance = (f'<p class="source">Fonte: {esc(data["source"])} · Extração: {extracted:%d/%m/%Y %H:%M:%S} UTC<br>'
                  f'Carga: <code>{esc(data["batch_id"])}</code><br>SHA-256 do JSON: <code>{report["sha256"]}</code></p>')
    pages = []

    def page(kicker, title, body):
        pages.append((kicker, title, body))

    page('BI · INVENTÁRIOS DE GASES DE EFEITO ESTUFA', 'Dados conectados.<br>Indicadores com<br><em>rastreabilidade.</em>',
         '<div class="cover-rule"></div><p class="cover-title">Relatório gerencial<br><strong>Aether</strong></p>'
         '<p class="cover-copy">Origem, tratamento, indicadores e rastreabilidade dos registros de emissões e reduções.</p>'
         f'<div class="cover-meta"><div><small>PERÍODO ANALISADO</small><strong>{period_label}</strong><span>{start:%d/%m/%Y} a {end:%d/%m/%Y}</span></div>'
         f'<div><small>EXTRAÇÃO DA GOLD</small><strong>{extracted:%d/%m/%Y}</strong><span>{extracted:%H:%M:%S} UTC</span></div></div>'
         + note('Escopo desta edição', data['scope_note']))

    page('VISÃO GERAL', 'A entrega em perspectiva',
         '<p>Indicadores consolidados a partir de <code>dados/relatorio.json</code>, exportado da Gold.</p>'
         + cards([(coverage['inventories'], 'inventários', 'Distintos no período'),
                  (coverage['enterprises'], 'empresas', 'Distintas no período'),
                  (coverage['units'], 'unidades', 'Distintas no período'),
                  (counts['fato_emissoes'], 'emissões', 'Registros na Gold')])
         + table(['Saída Gold', 'Linhas'], [(name, counts[name]) for name in TABLES], 'Volumes informados na exportação atual.')
         + note('Recorte dos resultados', f'Status: {", ".join(report["statuses"])}. Tipos: {", ".join(report["types"])}. '
                'Todos os grupos exportados são incluídos. Empresas e unidades são contagens distintas no período completo.') + provenance)

    page('1 · APRESENTAÇÃO DA BASE', 'Da estrutura organizacional<br>ao inventário',
         '<div class="flow vertical"><span>Aplicação Aether</span><b>↓</b><span>PostgreSQL / Aiven · data mart</span><b>↓</b><span>Bronze → Silver → Gold → Relatório</span></div>'
         + table(['View de entrada', 'Grão e finalidade'], [
             ('vw_dim_org_hierarchy', 'node_type + node_id: hierarquia organizacional.'),
             ('vw_fact_emission', 'emission_id: emissões por inventário e dimensões.'),
             ('vw_fact_reduction', 'reduction_id: reduções por inventário e categoria.'),
             ('vw_analytics_org_period', 'inventory_id: indicadores por inventário.')], 'Contratos descritos na documentação local.')
         + note('Recorte temporal', 'inventory_year representa o ano inicial do inventário. Períodos que atravessam anos não são rateados por este relatório.')
         + note('Proveniência', 'O JSON identifica a fonte e a carga exportada, mas não comprova a natureza operacional ou demonstrativa dos registros. A existência de massa fictícia local não determina a origem desta carga.'))

    page('2 · PROBLEMA E INDICADORES', 'Como interpretar as métricas',
         '<p>As métricas relacionam emissões e reduções com o período e a cobertura dos inventários disponíveis.</p>'
         + table(['Indicador', 'Regra e interpretação'], [
             ('Emissões', 'Soma das emissões registradas, em tCO₂e.'),
             ('Reduções', 'Soma das reduções registradas, em tCO₂e.'),
             ('Saldo', 'Emissões menos reduções. Pode ser negativo.'),
             ('Taxa de redução', '100 × soma das reduções ÷ soma das emissões. Não se aplica quando as emissões são zero.'),
             ('Cobertura', 'Inventários, empresas e unidades distintos no período. Não somar contagens anuais como entidades únicas.'),
             ('Categoria ausente', 'Percentual dos registros de emissão, não do volume em tCO₂e.')], 'Regras utilizadas pelo gerador.')
         + note('Limites gerenciais', 'Os totais não comprovam neutralidade ambiental nem causalidade. Sem produção ou receita, não há medida de intensidade operacional. Quantidades já em tCO₂e não recebem nova multiplicação por GWP.'))

    page('3 · ESTRATÉGIAS DE TRATAMENTO', 'Preservar, validar, conciliar',
         '<div class="stages"><div><b>01 · Bronze</b><p>Extração das views e identificação da carga.</p></div><div><b>02 · Silver</b><p>Padronização e validação dos dados.</p></div><div><b>03 · Gold</b><p>Conciliação e preparação das saídas analíticas.</p></div></div>'
         + table(['Etapa', 'Verificações descritas no código local'], [
             ('Silver', 'Textos, duplicatas, chaves, períodos, quantidades e integridade dos inventários.'),
             ('Gold', 'Conciliação dos fatos e dos indicadores por inventário.'),
             ('Exportação', 'Consistência de carga, chaves, quantidades e conciliação dos KPIs com os fatos.'),
             ('Gerador HTML', 'Contrato, números finitos, períodos, grupos duplicados, contagens, taxas e totais anuais versus resumo global.')])
         + note('Alcance das evidências', 'A descrição do código local não comprova a versão implantada. O JSON informa conciliação aprovada na exportação; não contém logs de correções da Silver nem todos os testes da Gold.'))

    for offset in range(0, len(years), 4):
        subset = years[offset:offset + 4]
        rows = [(year, *(br(value) for value in annual[year]), fmt_pct(rate(annual[year][1], annual[year][0]))) for year in subset]
        rows.append(('Total global', *(br(value) for value in totals), fmt_pct(rate(reduced, emitted))))
        page('4 · RESULTADOS OBSERVADOS', 'Resultados por ano',
             cards([(br(emitted), 'emissões · tCO₂e', 'Total global'), (br(reduced), 'reduções · tCO₂e', 'Total global'),
                    (br(balance), 'saldo · tCO₂e', 'Emissões − reduções'), (fmt_pct(rate(reduced, emitted)), 'taxa global', 'Razão entre somas')])
             + table(['Ano inicial', 'Emissões tCO₂e', 'Reduções tCO₂e', 'Saldo tCO₂e', 'Taxa'], rows,
                     f'Anos exibidos: {subset[0]} a {subset[-1]}. O total global considera todos os anos da carga.')
             + '<figure><figcaption>Taxa de redução por ano</figcaption>'
             + bars(subset, [rate(annual[year][1], annual[year][0]) for year in subset]) + '</figure>'
             + note('Interpretação', 'Os valores anuais somam todos os grupos de status e tipo. A série descreve os registros disponíveis, sem atribuir a evolução a ações ambientais.')
             + f'<p class="source">{coverage["inventories"]} inventários · Período global: {period_label}.</p>')

    page('5 · QUALIDADE E CONCILIAÇÃO', 'Totais conciliados,<br>lacunas explícitas',
         cards([(coverage['inventories'], 'inventários na carga', 'Conciliação aprovada na exportação'),
                (f'{missing} / {emission_count}', 'emissões sem categoria', fmt_pct(missing_pct, 2)),
                (quality['missing_gas_count'], 'emissões sem gás', 'Verificação por gas_id'),
                (quality['missing_scope_count'], 'emissões sem escopo', 'Verificação por scope_id')])
         + table(['Verificação', 'Resultado'], [
             ('Conciliação por inventário', 'Aprovada conforme o JSON da exportação.'),
             ('Grupos anuais × resumo global', 'Conferidos pelo gerador.'),
             ('Saldo e taxas', 'Recalculados pelo gerador.'),
             ('Empresa ausente nas emissões', quality['missing_enterprise_count']),
             ('Tolerância absoluta dos totais', '0,000001 tCO₂e.')])
         + note('Alcance', data['validation']['note'] + ' O JSON não informa os IDs nem a quantidade de inventários afetados pelas categorias ausentes.') + provenance)

    page('6 · PIPELINE E AUTOMAÇÃO', 'Rastreabilidade da carga',
         '<div class="flow"><span>Bronze</span><i>→</i><span>Silver</span><i>→</i><span>Gold</span><i>→</i><span>Exportação</span></div>'
         + table(['Metadado', 'Valor informado'], [
             ('Fonte', data['source']), ('Carga', data['batch_id']),
             ('Extração', extracted.strftime('%d/%m/%Y %H:%M:%S UTC')),
             ('Job de exportação', data['export_job_id'] or 'Não informado'),
             ('Execução de exportação', data['export_run_id'] or 'Não informada')])
         + note('Como conferir uma atualização', 'Compare a carga e a extração com o JSON no GitHub. O SHA-256 identifica os bytes exatos utilizados. Uma nova carga pode manter os indicadores iguais quando os dados de negócio não mudam.')
         + note('Limites dos metadados', 'O JSON não contém duração das tarefas, histórico de agendamento ou confirmação da publicação do HTML. Os IDs permitem localizar a execução no Databricks.') + provenance)

    page('7 · DASHBOARD E CAMADA DE CONSUMO', 'Cada pergunta, uma leitura',
         table(['Área de análise', 'Fonte prevista na arquitetura'], [
             ('Visão geral', 'kpis_inventario: totais, cobertura e evolução.'),
             ('Escopos, categorias e gases', 'fato_emissoes: distribuição das emissões.'),
             ('Empresas e unidades', 'Dimensões organizacionais relacionadas aos fatos.'),
             ('Reduções', 'fato_reducoes: quantidades e categorias.'),
             ('Qualidade', 'Ocorrências de preenchimento nas saídas analíticas.')], 'Referências da documentação e dos artefatos locais.')
         + note('Filtros e comparação', 'Ao comparar com o dashboard, use os mesmos períodos, status e tipos. Este HTML inclui todos os grupos do JSON e não possui filtros interativos.')
         + note('Verificação visual', 'O JSON agregado não comprova a configuração ou o funcionamento dos filtros do dashboard. Escopo de emissão não deve ser aplicado às reduções sem uma relação definida no modelo.'))

    page('8 · CONCLUSÕES E REFERÊNCIAS', 'Resultados com<br>rastreabilidade',
         f'<p>A carga contém <b>{coverage["inventories"]} inventários</b>, <b>{coverage["enterprises"]} empresas</b> e <b>{coverage["units"]} unidades</b>, de <b>{start:%d/%m/%Y}</b> a <b>{end:%d/%m/%Y}</b>.</p>'
         f'<p>Emissões: <b>{br(emitted)} tCO₂e</b>. Reduções: <b>{br(reduced)} tCO₂e</b>. Saldo: <b>{br(balance)} tCO₂e</b>. Taxa global: <b>{fmt_pct(rate(reduced, emitted))}</b>.</p>'
         + table(['Conferência', 'Encaminhamento'], [
             ('Categorias ausentes', f'Investigar {missing} registros de emissão sem categoria.'),
             ('Versão implantada', 'Conferir os notebooks executados com a versão documentada do código.'),
             ('Dashboard', 'Comparar indicadores com os mesmos filtros e testar a resposta visual.'),
             ('Proveniência', 'Confirmar a natureza demonstrativa ou operacional antes de interpretar desempenho ambiental.')])
         + '<h2>Referências e método</h2><ul class="references"><li>Dados: dados/relatorio.json, exportado da Gold.</li><li>Pipeline: Bronze, Silver, Gold e exportação no Databricks.</li><li>Documentação: README.md.</li><li>Cálculo: Decimal; taxas recalculadas a partir das somas; arredondamento apenas na apresentação.</li></ul>' + provenance)

    sections = []
    for index, (kicker, title, body) in enumerate(pages, 1):
        sections.append('<section class="page"><header><span class="brand">aether<span class="brand-dot">●</span></span><span>BUSINESS INTELLIGENCE · RELATÓRIO GERENCIAL</span></header>'
                        f'<main><div class="eyebrow">{esc(kicker)}</div><h1>{title}</h1>{body}</main>'
                        f'<footer><span>Aether · Extração {extracted:%d/%m/%Y}</span><span>{index:02d} / {len(pages):02d}</span></footer></section>')
    css = """
        :root {
            --ink: #0d3b35;
            --green: #1a5c4a;
            --mint: #a8f0d2;
            --violet: #5b21b6;
            --purple: #7c3aed;
            --lilac: #ede9fe;
            --muted: #56675f;
            --line: #d8e5df;
            --paper: #fff
        }

        * {
            box-sizing: border-box
        }

        html {
            background: #e6ede9
        }

        body {
            margin: 0;
            color: var(--ink);
            font-family: Arial,Helvetica,sans-serif;
            font-size: 10pt;
            line-height: 1.48;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact
        }

        .page {
            width: 210mm;
            height: 297mm;
            margin: 8mm auto;
            background: var(--paper);
            padding: 12mm 15mm 11mm;
            display: flex;
            flex-direction: column;
            position: relative;
            box-shadow: 0 8px 36px #0d3b351a
        }

        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--line);
            padding-bottom: 4mm;
            color: var(--muted);
            font-size: 6.8pt;
            letter-spacing: .07em;
            flex: none
        }

        .brand {
            font-size: 21pt;
            letter-spacing: -.04em;
            font-weight: 400;
            line-height: 1;
            color: var(--ink)
        }

        .brand-dot {
            color: #2dbd7e;
            font-size: 10pt;
            margin-left: 3px
        }

        main {
            padding-top: 7mm;
            flex: 1;
            min-height: 0
        }

        footer {
            border-top: 1px solid var(--green);
            padding-top: 3mm;
            display: flex;
            justify-content: space-between;
            font-size: 7pt;
            color: var(--muted);
            flex: none
        }

        .eyebrow {
            font-size: 8pt;
            font-weight: 700;
            letter-spacing: .14em;
            color: var(--violet);
            text-transform: uppercase;
            margin-bottom: 2mm
        }

        h1 {
            font-size: 27pt;
            line-height: 1.12;
            letter-spacing: -.035em;
            margin: 0 0 4mm;
            font-weight: 700
        }

        h2 {
            font-size: 13pt;
            line-height: 1.25;
            letter-spacing: -.02em;
            margin: 0 0 2.5mm
        }

        p {
            margin: 0 0 3.5mm
        }

        .lead {
            font-size: 11pt;
            color: var(--muted);
            margin-bottom: 5mm
        }

        .muted,small {
            color: var(--muted)
        }

        .source {
            font-size: 7.4pt;
            color: var(--muted);
            line-height: 1.4;
            margin-top: 2.5mm
        }

        .cards {
            display: grid;
            grid-template-columns: repeat(4,1fr);
            gap: 2.5mm;
            margin: 5mm 0
        }

        .card {
            padding: 3.5mm 2.5mm;
            background: #f5f9f6;
            border: 1px solid var(--line);
            border-top: 3px solid #2dbd7e;
            border-radius: 2.5mm
        }

        .card strong {
            font-size: 23pt;
            display: block;
            letter-spacing: -.035em;
            line-height: 1.2
        }

        .card span {
            display: block;
            font-size: 8.1pt;
            margin-top: 2mm
        }

        .card small {
            display: block;
            font-size: 7pt;
            margin-top: 2mm
        }

        .two {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 4mm;
            margin: 4mm 0
        }

        .panel {
            border: 1px solid var(--line);
            border-radius: 3mm;
            padding: 4.5mm;
            background: #fff
        }

        .big {
            font-size: 35pt;
            line-height: 1.2;
            font-weight: 700;
            margin: 4mm 0
        }

        .big span {
            font-size: 11pt;
            font-weight: 400
        }

        .contents {
            padding-left: 5mm;
            margin: 0;
            line-height: 1.95;
            font-size: 9pt
        }

        .note {
            background: #eef8f2;
            border-left: 3px solid var(--green);
            padding: 3.5mm 4mm;
            margin: 4mm 0;
            border-radius: 0 2mm 2mm 0;
            font-size: 9pt
        }

        .note p {
            margin: 1.5mm 0 0
        }

        .note.purple {
            background: var(--lilac);
            border-color: var(--violet)
        }

        table {
            border-collapse: collapse;
            width: 100%;
            margin: 4mm 0;
            font-size: 8.8pt;
            line-height: 1.4;
            table-layout: fixed
        }

        caption {
            text-align: left;
            font-size: 7.8pt;
            color: var(--muted);
            margin-bottom: 2mm
        }

        thead {
            display: table-header-group
        }

        th {
            background: var(--ink);
            color: #fff;
            text-align: left;
            padding: 2.6mm 2.7mm;
            font-size: 8pt;
            font-weight: 600
        }

        td {
            padding: 2.8mm 2.7mm;
            border-bottom: 1px solid var(--line);
            vertical-align: top;
            overflow-wrap: anywhere
        }

        tbody tr:nth-child(even) {
            background: #f5f9f6
        }

        th:first-child {
            width: 31%
        }

        code {
            font-family: Consolas,'Courier New',monospace;
            font-size: .86em;
            overflow-wrap: anywhere;
            color: var(--green)
        }

        .flow {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 3mm;
            margin: 4mm 0 5mm
        }

        .flow span {
            background: var(--ink);
            color: #fff;
            padding: 3mm 5mm;
            border-radius: 2mm;
            text-align: center;
            flex: 1
        }

        .flow span b {
            display: block;
            font-size: 8pt;
            color: var(--mint)
        }

        .flow i {
            font-style: normal;
            color: var(--violet)
        }

        .flow.vertical {
            flex-direction: column;
            gap: 1.3mm
        }

        .flow.vertical span {
            width: 100%;
            padding: 2mm
        }

        .stages {
            display: grid;
            grid-template-columns: repeat(3,1fr);
            gap: 3mm;
            margin: 5mm 0
        }

        .stages div {
            background: var(--lilac);
            padding: 3mm;
            border-radius: 2mm;
            font-size: 8.6pt
        }

        .stages b {
            color: var(--violet)
        }

        .stages p {
            margin: 2mm 0 0
        }

        figure {
            margin: 4mm 0;
            padding: 4mm;
            border: 1px solid var(--line);
            border-radius: 3mm
        }

        figcaption {
            font-size: 9pt;
            margin-bottom: 2mm
        }

        figcaption span {
            display: block;
            color: var(--muted);
            font-size: 8pt
        }

        svg {
            display: block;
            width: 100%;
            height: auto
        }

        figure.evidence {
            padding: 2mm
        }

        figure.evidence img {
            display: block;
            width: 100%;
            height: auto;
            border-radius: 1mm
        }

        figure.evidence figcaption {
            font-size: 7.2pt;
            margin: 2mm 0 0
        }

        .references {
            font-size: 7.6pt;
            line-height: 1.4;
            padding-left: 4mm;
            margin: 2mm 0
        }

        .references li {
            margin-bottom: 1.8mm
        }

        tr,figure,.panel,.card,.note,.stages {
            break-inside: avoid
        }

        h1,h2,h3,figcaption {
            break-after: avoid
        }

        .page:first-of-type {
            background: var(--ink);
            color: #fff
        }

        .page:first-of-type header,.page:first-of-type footer {
            color: #a8f0d2;
            border-color: #397168
        }

        .page:first-of-type .brand {
            color: #fff
        }

        .page:first-of-type .eyebrow {
            color: var(--mint);
            margin-top: 9mm
        }

        .page:first-of-type h1 {
            font-size: 42pt;
            line-height: 1.06;
            margin-top: 6mm;
            letter-spacing: -.045em
        }

        .page:first-of-type em {
            font-style: normal;
            color: var(--mint)
        }

        .cover-rule {
            width: 23mm;
            height: 1mm;
            background: #7c3aed;
            margin: 8mm 0
        }

        .cover-title {
            font-size: 19pt;
            line-height: 1.3
        }

        .cover-title strong {
            font-size: 30pt
        }

        .cover-copy {
            max-width: 135mm;
            font-size: 12pt;
            color: #d7eee4;
            margin-top: 4mm
        }

        .cover-meta {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 5mm;
            margin: 9mm 0
        }

        .cover-meta small,.cover-meta strong,.cover-meta span {
            display: block
        }

        .cover-meta small {
            font-size: 7pt;
            letter-spacing: .08em;
            color: var(--mint)
        }

        .cover-meta strong {
            font-size: 16pt;
            margin: 2mm 0
        }

        .cover-meta span {
            font-size: 8pt;
            color: #d7eee4
        }

        .page:first-of-type .note {
            background: #194d43;
            color: #edf8f2;
            border-color: #5ddba5;
            font-size: 8.5pt
        }

        .palette-strip {
            display: flex;
            gap: 2mm;
            margin-top: 5mm
        }

        .palette-strip i {
            height: 2mm;
            flex: 1;
            background: #1a5c4a
        }

        .palette-strip i:nth-child(2) {
            background: #2dbd7e
        }

        .palette-strip i:nth-child(3) {
            background: #a8f0d2
        }

        .palette-strip i:nth-child(4) {
            background: #7c3aed
        }

        .palette-strip i:nth-child(5) {
            background: #c4b5fd
        }

        .page:nth-of-type(6) .card strong {
            font-size: 15pt
        }

        .page:nth-of-type(6) th:first-child {
            width: 14%
        }

        .page:nth-of-type(7) th:first-child {
            width: 32%
        }

        .page:nth-of-type(7) th:nth-child(2) {
            width: 23%
        }

        .page:nth-of-type(8) table {
            font-size: 8.1pt
        }

        .page:nth-of-type(8) td {
            padding: 2mm 2.7mm
        }

        .page:last-of-type table {
            font-size: 8pt
        }

        .page:last-of-type td {
            padding: 2mm 2.7mm
        }

        .toolbar {
            max-width: 210mm;
            margin: 6mm auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: var(--green);
            font-size: 9pt
        }

        .toolbar button {
            background: var(--violet);
            color: white;
            border: 0;
            border-radius: 6px;
            padding: 10px 18px;
            cursor: pointer;
            font: inherit
        }

        .toolbar span {
            padding-right: 10px
        }

        .page:nth-of-type(6) figure {
            margin: 3mm 0;
            padding: 3mm
        }

        .page:nth-of-type(6) svg {
            max-height: 49mm
        }

        .page:nth-of-type(6) .note {
            font-size: 8.5pt;
            padding: 3mm
        }

        .page:nth-of-type(8) figure.evidence {
            margin: 3mm 0
        }

        .page:nth-of-type(8) figure.evidence img {
            max-height: 68mm;
            object-fit: contain
        }

        .page:nth-of-type(8) .note {
            font-size: 8pt;
            padding: 2.5mm;
            margin: 3mm 0
        }

        .page:nth-of-type(8) .source {
            font-size: 6.9pt
        }

        @page {
            size: A4 portrait;
            margin: 0
        }

        @media print {
            html,body {
                background: #fff;
                margin: 0
            }
            .toolbar {
                display: none
            }
            .page {
                margin: 0;
                box-shadow: none;
                break-after: page;
                page-break-after: always
            }
            .page:last-of-type {
                break-after: auto;
                page-break-after: auto
            }
            a {
                color: inherit;
                text-decoration: none
            }
        }

        @media screen and (max-width:810px) {
            .page {
                margin: 4mm 0
            }
            .toolbar {
                padding: 0 4mm
            }
        }

        .source, code {
            overflow-wrap: anywhere;
        }

        .source code {
            font-size: 6.5pt;
        }

        .card {
            min-width: 0;
        }

        .card strong {
            overflow-wrap: anywhere;
        }
    """
    return ('<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="description" content="Relatório gerencial Aether gerado dos indicadores exportados da Gold.">'
            f'<meta name="aether-json-sha256" content="{report["sha256"]}"><meta name="aether-batch-id" content="{esc(data["batch_id"])}">'
            f'<title>Aether | Relatório gerencial de BI</title><style>{css}</style></head><body>'
            f'<div class="toolbar"><span>Relatório Aether · A4 · {len(pages)} páginas<br>Na impressão: escala 100%, sem cabeçalhos do navegador.</span>'
            '<button onclick="window.print()">Imprimir / Salvar como PDF</button></div>' + ''.join(sections) + '</body></html>')


def build(data_path, output_path):
    data_path, output_path = Path(data_path), Path(output_path)
    if data_path.resolve() == output_path.resolve():
        raise ValueError('Entrada JSON e saida HTML devem ser diferentes.')
    with localcontext() as context:
        context.prec = 80
        report = load_report(data_path)
        document = render_report(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output_path.parent,
                                        prefix='.relatorio-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(document)
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return report


ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "dados/relatorio.json")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/relatorio_gerencial/relatorio-gerencial-aether.html")
    args = parser.parse_args()
    try:
        report = build(args.data, args.output)
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        parser.exit(1, f"Falha ao gerar relatorio: {exc}\n")
    print(f"Relatorio gerado: {args.output}")
    print(f"Carga: {report['data']['batch_id']}")
    print(f"SHA-256 do JSON: {report['sha256']}")

if __name__ == "__main__":
    main()
