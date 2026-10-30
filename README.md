# Aether BI

Relatório gerencial de emissões e reduções, gerado com os dados da Gold do Databricks.

## Organização

- Branch `main`: gerador Python, documentação e workflow de publicação.
- Branch `dados`: `dados/relatorio.json` e um pequeno workflow que sinaliza as atualizações.
- O Python e os estilos ficam em `scripts/build_relatorio_gerencial.py`, na `main`.

## Atualização automática

1. O Job do Databricks publica o JSON na branch `dados`.
2. O workflow **Dados Aether atualizados**, nessa branch, sinaliza a mudança.
3. O workflow **Publicar relatorio Aether**, na `main`, busca o código da `main` e o JSON mais recente da `dados`.
4. O gerador valida os dados e cria `public/index.html`.
5. O GitHub Pages publica o HTML no endereço exibido em **Settings → Pages**.

Mudanças no gerador ou no workflow da `main` também publicam uma nova versão. A publicação não cria commit de HTML. Se a validação falhar, o site mantém a versão anterior. Cargas sucessivas são processadas sem publicações simultâneas.

O relatório mostra a identificação da carga, a extração UTC e o SHA-256 do JSON. O resumo da execução no Actions também registra os commits do código e dos dados utilizados. Uma nova carga pode manter os indicadores iguais quando os dados de negócio não mudam.

## Configuração inicial

- Em **Settings → Pages → Build and deployment → Source**, selecione **GitHub Actions**.
- O workflow `.github/workflows/publicar-relatorio.yml` precisa estar na `main`.
- Na branch `dados`, crie `.github/workflows/dados-atualizados.yml` com o nome de workflow **Dados Aether atualizados**. Esse nome deve corresponder ao filtro `workflow_run` da publicação.
- O ambiente `github-pages` deve permitir publicações da `main`. Para funcionamento sem aprovação manual, não configure revisores obrigatórios nesse ambiente.
- Se a organização restringir Actions, permita as ações oficiais `actions/checkout`, `actions/setup-python`, `actions/configure-pages`, `actions/upload-pages-artifact` e `actions/deploy-pages`.

Não é necessário outro token no Databricks para acionar essa publicação. O exportador continua enviando apenas o JSON e precisa manter o parâmetro `branch` como `dados`.

## Testar

Para a primeira publicação, acesse **Actions → Publicar relatorio Aether → Run workflow**, selecionando `main`.
Depois rode o Job no Databricks e confira os dois workflows: **Dados Aether atualizados** e **Publicar relatorio Aether**. Compare a carga e a extração exibidas no site com o JSON da branch `dados`.

O workflow usa sempre o JSON mais recente disponível quando começa a execução. O commit efetivamente utilizado fica registrado no resumo da execução.

## Gerar localmente

Requisito: Python 3.10 ou superior, sem bibliotecas adicionais. Obtenha uma cópia do JSON da branch `dados` e execute:

```shell
python scripts/build_relatorio_gerencial.py --data caminho/relatorio.json --output caminho/relatorio.html
```

O HTML é autocontido. Abra no navegador; para exportar em PDF, use A4, escala 100% e cabeçalhos/rodapés do navegador desativados.
O arquivo em `outputs/` é uma cópia gerada anteriormente; a atualização automática publica o site, sem atualizar essa cópia no Git.

Credenciais e tokens devem permanecer fora do repositório.
