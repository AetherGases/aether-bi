# Aether BI

Relatório gerencial de emissões e reduções, gerado a partir dos dados da Gold do Databricks.

## Arquivos

- `dados/relatorio.json`: dados atualizados pelo exportador do Databricks.
- `scripts/build_relatorio_gerencial.py`: lê o JSON, confere os valores e monta o relatório. Os estilos já estão dentro dele.
- `outputs/relatorio_gerencial/relatorio-gerencial-aether.html`: relatório pronto para abrir no navegador.

O README explica o uso, LICENSE contém a licença e .gitignore evita incluir arquivos temporários no Git.

## Gerar o relatório

Requisito: Python 3.10 ou superior. Não precisa instalar bibliotecas.

Na raiz do repositório:

```shell
python scripts/build_relatorio_gerencial.py
```

Depois abra `outputs/relatorio_gerencial/relatorio-gerencial-aether.html` no navegador.
Para salvar em PDF, use o botão de impressão: papel A4, escala 100% e cabeçalhos/rodapés do navegador desativados.

## Como funciona

O JSON contém os números; o Python gera cards, tabelas e gráficos; o HTML mostra o resultado.
O relatório exibe a carga, a data da extração em UTC e o hash do JSON utilizado.
Se os dados estiverem inválidos ou os totais não fecharem, a geração é interrompida e o HTML anterior é preservado.
Empresas e unidades são contadas distintamente no período, sem somar as contagens anuais.

Quando o JSON mudar, execute o comando novamente. A geração e publicação automáticas pelo GitHub Actions serão configuradas na próxima etapa.

Credenciais e tokens não devem ser incluídos no repositório.
