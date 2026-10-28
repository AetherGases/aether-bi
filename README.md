# Aether BI

Repositório de Business Intelligence do ecossistema **Aether**, voltado à análise de inventários de gases de efeito estufa (GEE). Reúne os notebooks de processamento, as consultas SQL, os indicadores e o relatório gerencial do projeto.

O objetivo é transformar os registros da aplicação em informações sobre emissões, reduções e qualidade dos dados, permitindo análises por empresa, unidade, departamento e período.

## Fluxo dos dados

```text
PostgreSQL / Aiven → Views do data mart → Bronze → Silver → Gold → Dashboard
```

O processamento acontece no Databricks, seguindo a arquitetura medalhão:

- **Bronze:** extrai as views do data mart e registra a origem e a identificação da carga.
- **Silver:** padroniza os dados e valida chaves, períodos, quantidades e relacionamentos.
- **Gold:** concilia os totais e prepara as tabelas e os indicadores para consumo.

As três etapas são executadas em sequência pelo Job **Aether — Pipeline**, com agendamento diário.

## Análises e indicadores

O BI acompanha emissões e reduções em **tCO₂e**, saldo após reduções, taxa de redução e cobertura dos inventários. Também permite explorar a distribuição por escopo, categoria e gás, além de identificar informações incompletas.

Os resultados alimentam o dashboard no Databricks e o relatório gerencial em HTML, preparado para exportação em PDF. A massa de demonstração disponível no repositório é fictícia e destinada ao desenvolvimento e à validação do projeto.

## Organização

| Pasta | Conteúdo |
|---|---|
| `database/` | Views do data mart e scripts SQL de apoio |
| `notebooks/` | Etapas Bronze, Silver e Gold do Databricks |
| `docs/` | Arquitetura, regras dos indicadores e documentação da entrega |
| `scripts/` | Geração, exportação e validações locais |
| `outputs/` | Arquivos do dashboard, dados exportados e relatório gerencial |

## Atualização do relatório

A próxima etapa é integrar a publicação dos resultados ao Job: após o sucesso da Gold, uma tarefa exportará os indicadores para este repositório, e um workflow do GitHub Actions gerará o HTML atualizado.

O relatório atual é estático; essa integração ainda está em implementação. Credenciais e tokens de acesso devem permanecer fora do repositório.
