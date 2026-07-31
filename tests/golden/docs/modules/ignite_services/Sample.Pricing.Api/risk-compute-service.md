---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Grid.RiskComputeService`0
  doc_path: docs/modules/ignite_services/Sample.Pricing.Api/risk-compute-service.md
  title: RiskComputeService
  fqn: Sample.Pricing.Api.Grid.RiskComputeService
  kind: ignite_service
  template: ignite-service
  template_ref: templates/ignite-service.md
  example_ref: null
  module: Sample.Pricing.Api
  module_csproj: src/Sample.Pricing.Api/Sample.Pricing.Api.csproj
  domain: Sample.Pricing.Api
  team: null
  signature_hash: sha256:d5fc934fac3812b5ac007eefd293e5a03c183dcc5ba2a0616c11f71c9e887665
  impl_hash: sha256:7006712d45f5bc29c8408a7a2ce9dd08d32703a6ff025a07ddce5555255a2ae8
  ruleset_version: 2026-07-30.1
  sources:
  - path: src/Sample.Pricing.Api/Grid/RiskComputeService.cs
    start: 6
    end: 11
docpipe_state:
  accepted: null
  review: null
---

# RiskComputeService

<!-- docpipe:generated:start -->
`Sample.Pricing.Api.Grid.RiskComputeService` — public sealed class, модуль `Sample.Pricing.Api`, домен `Sample.Pricing.Api`, владелец не задан.

### Исходники

- [`src/Sample.Pricing.Api/Grid/RiskComputeService.cs`](../../../../src/Sample.Pricing.Api/Grid/RiskComputeService.cs) — строки 6–11

### HTTP-эндпоинты

Нет.

### Зависимости

Нет.

### Связи

Нет.

### XML-doc из кода

> Risk aggregation running on the compute grid.
<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
<!-- Зачем сервис живёт в кластере, а не в приложении. -->
<!-- docpipe:section:end purpose -->

## Развёртывание в кластере

<!-- docpipe:section:start deployment -->
<!-- Сколько экземпляров, на каких узлах, как регистрируется. -->
<!-- docpipe:section:end deployment -->

## Контракт сервиса

<!-- docpipe:section:start cluster_contract -->
<!-- Какие методы вызываются через прокси и что они гарантируют.
     Имена методов здесь — часть контракта: их зовут по имени. -->
<!-- docpipe:section:end cluster_contract -->

## Отказы

<!-- docpipe:section:start failure_modes -->
<!-- Что происходит при падении узла, переразвёртывании, недоступности кластера. -->
<!-- docpipe:section:end failure_modes -->

## Замечания

<!-- docpipe:section:start notes -->
<!-- Известные ограничения, легаси, планы. Пусто — нормально. -->
<!-- docpipe:section:end notes -->
