# PowerOps для Kolla-Ansible 2025.1: Ironic HA, Masakari fencing и Mistral

Эта инструкция описывает единый путь установки и эксплуатации из одного
checkout Kolla-Ansible. Имена узлов, VLAN и адреса из примера являются
документационными. Перед работой с целевой средой их необходимо заменить и
проверить.

Архитектурная схема доступна в двух форматах:

- [редактируемый SVG](ironic-ha-power-workflows.svg);
- [готовый PNG](ironic-ha-power-workflows.png).

## Публикация материалов

Материалы опубликованы в ветке `main`:

- [Репозиторий IRNMN](https://github.com/lebtmalorny-rgb/IRNMN);
- [подробная инструкция](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/docs/powerops/POWEROPS-INSTALL.md);
- [редактируемая SVG-схема](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/docs/powerops/ironic-ha-power-workflows.svg);
- [PNG-схема](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/docs/powerops/ironic-ha-power-workflows.png);
- [globals.yml](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/etc/kolla/globals.yml);
- [конфигурация Ironic](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/etc/kolla/config/ironic.conf);
- [конфигурация Masakari Engine](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/etc/kolla/config/masakari/masakari-engine.conf);
- [конфигурация Mistral Executor](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/etc/kolla/config/mistral/mistral-executor.conf);
- [кастомный Masakari TaskFlow `IronicFenceTask`](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/plugins/masakari_ironic_fence/src/masakari_ironic_fence/task.py);
- [регистрация Masakari TaskFlow entry point](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/plugins/masakari_ironic_fence/pyproject.toml);
- [исходники custom actions для Mistral](https://github.com/lebtmalorny-rgb/IRNMN/tree/main/plugins/mistral_power_actions/src/openstack_power_actions);
- [Mistral workbook `power_ops`](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/mistral/workbooks/power-ops.yaml);
- [отчёт проверки](https://github.com/lebtmalorny-rgb/IRNMN/blob/main/reports/powerops-validation.json).

Граница этой поставки: Ironic хранит соответствие compute host, BMC и MAC,
выполняет только power/management операции и оставляет Nodes в
`manageable`. В inventory отсутствуют `nova-compute-ironic`, inspector, TFTP
и HTTP provisioning services. Masakari всегда выполняет физический fencing
до подготовки и evacuation экземпляров. Mistral обслуживает только плановые
операции и не вызывает Masakari evacuation.

В этом workspace выполнены локальные тесты, разбор конфигураций, сборка wheel
и Ansible syntax-check. Доступ к целевым control nodes, BMC, OpenStack API и
private registry не предоставлялся. Причина live-статуса в отчёте:
`No target deployment was authorized`.

## Подготовка checkout и deployment host

Все дальнейшие действия выполняются из одного каталога `/opt/kolla-ansible`
на deployment host. `tools/powerops`, исходники plugins, роль и документация
остаются в checkout. На compute hosts эти скрипты не копируются. Custom
Ansible modules передаются Ansible во временный каталог только на время
вызова API.

### Шаг 1. Установить системные пакеты

**Где:** deployment host.

**Текущий каталог:** любой каталог с правами администратора.

**Входной файл:** отсутствует; используются пакеты ОС.

**Что делает:** устанавливает Python, build tools, Git, Podman и XML utility.

**Куда попадает:** системные каталоги deployment host.

**Кто использует:** Python virtual environment, Kolla-Ansible, image builder
и локальные проверки.

**Команда для Rocky Linux или SberLinux:**

```bash
sudo dnf install -y python3 python3-devel gcc git podman libxml2
```

**Команда для Debian или Ubuntu:**

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-dev build-essential git podman libxml2-utils
```

**Ожидаемый результат:** `python3`, `git`, `podman` и `xmllint` завершаются с
кодом `0` при запросе версии.

**Безопасный повтор:** package manager пропускает уже установленные пакеты.

**Откат:** удаление общесистемных пакетов не рекомендуется, пока ими
пользуются другие deployment workflows.

### Шаг 2. Подготовить checkout-local virtual environment

**Где:** deployment host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** `setup.cfg`, `requirements.yml`, `requirements-core.yml`.

**Что делает:** устанавливает Kolla-Ansible из этого checkout; именно поэтому
добавленные `site.yml`, `powerops.yml` и роль используются одной командой.

**Куда попадает:** `/opt/kolla-ansible/.venv`.

**Кто использует:** оператор, `kolla-ansible`, `tools/powerops` и delegated
API modules.

**Команда:**

```bash
cd /opt/kolla-ansible
python3 -m venv .venv
source "$PWD/.venv/bin/activate"
python3 -m pip install --upgrade pip
python3 -m pip install -e .
export KOLLA_CONFIG_PATH="$PWD/etc/kolla"
kolla-ansible install-deps --configdir "$PWD/etc/kolla" \
  -i "$PWD/etc/kolla/inventory"
```

**Ожидаемый результат:** `kolla-ansible --version` указывает на `.venv`, а
Ansible collections установлены без ошибки.

**Безопасный повтор:** editable install и `install-deps` повторяемы.

**Откат:** деактивировать environment командой `deactivate`; удаление `.venv`
не меняет remote hosts или OpenStack records.

### Шаг 3. Подготовить passwords и локально проверить входные данные

**Где:** deployment host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** `etc/kolla/globals.yml`, `etc/kolla/inventory`.

**Что делает:** ограничивает чтение globals, генерирует Kolla passwords и
создаёт redacted validation report.

**Куда попадает:** `etc/kolla/passwords.yml` и
`reports/powerops-validation.json`.

**Кто использует:** Kolla variable loader, PowerOps validator и оператор.

**Команда:**

```bash
cd /opt/kolla-ansible
chmod 0600 "$PWD/etc/kolla/globals.yml"
kolla-genpwd -p "$PWD/etc/kolla/passwords.yml"
chmod 0600 "$PWD/etc/kolla/passwords.yml"
tools/powerops validate \
  --configdir "$PWD/etc/kolla" \
  --inventory "$PWD/etc/kolla/inventory" \
  --report "$PWD/reports/powerops-validation.json"
```

**Ожидаемый результат:** `local_validation.status` равен `passed`; отчёт
содержит два Nodes и два Ports, но не содержит `driver_info` или password.

**Безопасный повтор:** validator перезаписывает только redacted report;
`kolla-genpwd` сохраняет уже заполненные значения.

**Откат:** восстановить `passwords.yml` из защищённой резервной копии. Не
копировать этот файл в Git.

## Итоговое дерево файлов

```text
kolla-ansible/
├── ansible/
│   ├── powerops.yml
│   ├── site.yml
│   └── roles/powerops/
│       ├── defaults/main.yml
│       ├── library/
│       │   ├── powerops_ironic_node.py
│       │   ├── powerops_masakari_segment.py
│       │   └── powerops_mistral_workbook.py
│       └── tasks/
├── docker/powerops/
│   ├── masakari/Containerfile
│   └── mistral/Containerfile
├── etc/kolla/
│   ├── config/ironic.conf
│   ├── config/masakari/masakari-engine.conf
│   ├── config/mistral/mistral-executor.conf
│   ├── globals-pvs-fragment.yml
│   ├── globals.yml
│   └── inventory
├── mistral/workbooks/power-ops.yaml
├── plugins/
│   ├── masakari_ironic_fence/
│   └── mistral_power_actions/
├── reports/powerops-validation.json
└── tools/powerops
```

Путь данных и потребитель:

| Источник | Куда попадает | Кто использует | Сохраняется после deploy |
|---|---|---|---|
| `etc/kolla/globals.yml` | Ansible variables | Kolla и роль PowerOps | только deployment host |
| `etc/kolla/inventory` | Ansible inventory model | Kolla plays | только deployment host |
| `etc/kolla/config/ironic.conf` | merged `ironic.conf` в API/conductor containers | Ironic | в container config volumes |
| `etc/kolla/config/masakari/masakari-engine.conf` | `/etc/masakari/masakari.conf` в `masakari_engine` | Masakari TaskFlow | в container config volume |
| `etc/kolla/config/mistral/mistral-executor.conf` | merged executor `mistral.conf` | Mistral executor | в container config volume |
| `plugins/masakari_ironic_fence` | `masakari_ironic_fence` wheel, затем derived image | `masakari_engine` | в image registry и container venv |
| `plugins/mistral_power_actions` | `openstack_power_actions` wheel, затем три derived images | Mistral API, engine и executor | в image registry и container venv |
| `powerops_ironic_nodes` | Node/Port records | Ironic API/DB | да; BMC secrets не попадают в report |
| redacted host projection | segment/host records | Masakari API/DB | да |
| `mistral/workbooks/power-ops.yaml` | workbook/workflow records | Mistral API/DB | да |
| docs, tests, SVG, PNG, reports | файлы checkout | оператор и CI | только deployment host |

## Inventory

Пример содержит три control-plane узла и два обычных Nova compute узла. Ironic
API и conductors работают на всех controllers. Пустые группы inspector, TFTP,
HTTP и `nova-compute-ironic` фиксируют power-only границу.

### Шаг 4. Проверить inventory до обращения к узлам

**Где:** deployment host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** `etc/kolla/inventory`.

**Что делает:** строит inventory graph без SSH и показывает membership.

**Куда попадает:** только stdout.

**Кто использует:** оператор и Ansible.

**Команда:**

```bash
cd /opt/kolla-ansible
ansible-inventory -i "$PWD/etc/kolla/inventory" --graph
```

**Ожидаемый результат:** `controller-01..03` входят в `control`, Ironic API и
conductor; `compute-01..02` входят в `compute`; provisioning groups пусты.

**Безопасный повтор:** read-only.

**Откат:** не требуется.

## Globals и BMC records

`powerops_ironic_nodes` является единственным декларативным источником Node,
BMC и Port mappings. Имена и IP являются примерами. Для Redfish обязательны
address, system ID, username и password; для IPMI обязательны address,
username и password. `network_interface=noop` и
`desired_provision_state=manageable` менять нельзя.

По согласованному упрощению BMC passwords пока находятся в `globals.yml`.
Файл должен иметь mode `0600`. Перед production рекомендуется перенести
секреты в Ansible Vault или внешний secret manager. Если Ironic API возвращает
password как `******`, роль повторно применяет объявленное значение на
`reconfigure`; secret не появляется в Ansible result.

### Шаг 5. Заменить примерные BMC records

**Где:** deployment host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** `etc/kolla/globals.yml`.

**Что делает:** задаёт точные Node names, `nova_hostname`, Redfish/IPMI
endpoints и MAC interfaces.

**Куда попадает:** после deploy данные записываются через Ironic API; файл
остаётся на deployment host.

**Кто использует:** validator и `powerops_ironic_node`.

**Команда:**

```bash
cd /opt/kolla-ansible
chmod 0600 "$PWD/etc/kolla/globals.yml"
tools/powerops validate \
  --configdir "$PWD/etc/kolla" \
  --inventory "$PWD/etc/kolla/inventory" \
  --report "$PWD/reports/powerops-validation.json"
```

**Ожидаемый результат:** нет duplicate name, MAC или Redfish system; каждый
`nova_hostname` присутствует в `[compute]`.

**Безопасный повтор:** validation read-only относительно OpenStack.

**Откат:** восстановить защищённую копию globals до запуска deploy.

## PVS/SberLinux registry fragment

`etc/kolla/globals-pvs-fragment.yml` является только примером overlay. Kolla
его автоматически не объединяет. После review оператор копирует нужные ключи
в `globals.yml`: distro/release/tag, registry/namespace/prefix и обе image
mappings. Значения registry также примерные.

### Шаг 6. Применить проверенные PVS keys вручную

**Где:** deployment host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** `etc/kolla/globals-pvs-fragment.yml`.

**Что делает:** показывает overlay, затем проверяет вручную объединённый
`globals.yml`; команда не печатает значения password.

**Куда попадает:** reviewed keys переносятся только в `globals.yml`.

**Кто использует:** Kolla image variables и `tools/powerops build-images`.

**Команда:**

```bash
cd /opt/kolla-ansible
python3 -c "import yaml; p='etc/kolla/globals-pvs-fragment.yml'; d=yaml.safe_load(open(p)); print(yaml.safe_dump(d, sort_keys=True))"
tools/powerops validate \
  --configdir "$PWD/etc/kolla" \
  --inventory "$PWD/etc/kolla/inventory" \
  --report "$PWD/reports/powerops-validation.json"
```

**Ожидаемый результат:** base и derived mappings содержат четыре одинаковых
service keys; runtime image variables ссылаются на derived mappings.

**Безопасный повтор:** просмотр и validation повторяемы.

**Откат:** вернуть предыдущие reviewed registry keys из локальной копии.

## Service overrides

Ironic override включает только `redfish`, `ipmi`, power/management interfaces
и `noop` network. `automated_clean=false`; boot/deploy noop interfaces не
изобретаются. Masakari использует фактические имена options 2025.1:
`host_auto_failure_recovery_tasks` и `host_rh_failure_recovery_tasks`. Оба
порядка равны `disable → ironic_fence → prepare → evacuate`. Mistral executor
получает Kolla Redis Sentinel endpoints, owner-safe lock TTL и polling limits.

### Шаг 7. Проверить overrides до prechecks

**Где:** deployment host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** три файла под `etc/kolla/config`.

**Что делает:** запускает release-blocking tests power-only и fencing order.

**Куда попадает:** только pytest output.

**Кто использует:** оператор и CI.

**Команда:**

```bash
cd /opt/kolla-ansible
python3 -m pytest tests/powerops/test_overrides.py tests/powerops/test_safety_invariants.py -v
```

**Ожидаемый результат:** все tests проходят; provisioning API calls отсутствуют.

**Безопасный повтор:** read-only.

**Откат:** восстановить предыдущие override files и повторить tests.

## Сборка и публикация images

Исходники plugins сами в containers не копируются. `build-images` собирает два
wheel, проверяет entry points, помещает wheel в matching build context и
строит четыре derived images: `mistral-api`, `mistral-engine`,
`mistral-executor`, `masakari-engine`. Mistral event engine остаётся на
upstream image, потому что не обнаруживает и не исполняет custom actions.

### Что находится в wheel-пакетах

Wheel — это устанавливаемый архив Python-пакета. Он нужен не как отдельный
скрипт оператора, а как способ штатно установить Python-модули и
`entry_points` во встроенное virtual environment Kolla-контейнера. Исходником
остаётся каталог `plugins`; wheel является воспроизводимым build artifact и в
Git не сохраняется.

В комплекте собираются два wheel:

| Wheel | Исходник | Что регистрирует | Куда устанавливается | Кто использует |
|---|---|---|---|---|
| `masakari_ironic_fence-1.0.0-py3-none-any.whl` | `plugins/masakari_ironic_fence` | `masakari.task_flow.tasks: ironic_fence` → `IronicFenceTask` | derived `masakari-engine` image | Masakari TaskFlow при аварийном host recovery |
| `openstack_power_actions-1.0.0-py3-none-any.whl` | `plugins/mistral_power_actions` | 15 entry points группы `mistral.actions` с namespace `powerops.*` | derived `mistral-api`, `mistral-engine` и `mistral-executor` images | Mistral action discovery, workflow engine и executor |

`masakari_ironic_fence` содержит:

- `task.py` — класс `IronicFenceTask`, который находит ровно один Ironic Node
  по имени compute host, запрашивает `power off` и требует заданное число
  стабильных подтверждений выключения;
- `config.py` — параметры OpenStack-аутентификации, timeout, poll interval и
  число стабильных наблюдений;
- entry point `ironic_fence`, через который Masakari загружает task по имени
  из `masakari-engine.conf`.

В Masakari получается следующая TaskFlow-цепочка:

```text
pre:  disable_compute_service_task -> ironic_fence
main: prepare_HA_enabled_instances_task
post: evacuate_instances_task
```

Только `ironic_fence` является кастомным TaskFlow task. Остальные элементы —
штатные задачи Masakari. Если Ironic не подтвердил физический `power off`,
кастомный task завершает flow ошибкой; подготовка и evacuation не должны
продолжаться. Автоматического обратного включения в `revert()` нет.

`openstack_power_actions` содержит:

- `actions.py` — 15 классов Mistral actions, включая блокировку host,
  disable/enable Nova service, maintenance flag Masakari, drain, проверки
  пустого host, Ironic power и fail-safe обработку;
- `clients.py` — создание OpenStack clients из service credentials;
- `locks.py` — distributed host lock через Kolla Redis Sentinel;
- `operations.py` — безопасная последовательность Nova, Masakari, Ironic и
  libvirt-проверок.

Одинаковый Mistral wheel устанавливается в API, engine и executor, чтобы все
три runtime имели один набор Python-модулей и entry points. Action records
заполняются командой `mistral-db-manage populate` в `mistral_engine`, а
непосредственное выполнение actions происходит в `mistral_executor`.

Путь wheel от исходника до runtime:

```text
plugins/*/src
  -> python -m build
  -> plugins/*/dist/*.whl
  -> docker/powerops/<component>/dist/*.whl
  -> Containerfile: pip install в /var/lib/kolla/venv
  -> derived image
  -> private registry
  -> service container на control plane
```

Wheel не копируется напрямую на compute hosts. После публикации Kolla скачивает
derived images на control plane и создаёт из них service containers. Версии
wheel и derived image tag должны изменяться согласованно. Для rollback нужно
возвращать прежний image tag целиком, а не удалять отдельный Python-пакет из
работающего контейнера.

### Шаг 8. Собрать derived images

**Где:** deployment host с доступом к base-image registry.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** plugin packages, Containerfiles и image mappings globals.

**Что делает:** создаёт wheel и локальные Podman images; push не выполняется.

**Куда попадает:** ignored `plugins/*/dist`, `docker/powerops/*/dist` и local
container storage.

**Кто использует:** последующая publish phase и Kolla pull/deploy.

**Команда:**

```bash
cd /opt/kolla-ansible
tools/powerops build-images --configdir "$PWD/etc/kolla"
```

**Ожидаемый результат:** четыре fully qualified derived tags доступны через
`podman image inspect`.

**Безопасный повтор:** один и тот же tag пересобирается локально; push не
запускается неявно.

**Откат:** удалить только явно выбранные local derived image IDs после
проверки, что containers их не используют.

**Статус:** NOT RUN IN THIS WORKSPACE: Podman отсутствовал.

### Шаг 9. Опубликовать derived images

**Где:** deployment host с login в reviewed registry.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** `powerops_derived_images` из globals и четыре local images.

**Что делает:** сначала inspect всех images, затем push; registry host должен
точно совпасть с `--confirm-registry`.

**Куда попадает:** private image registry.

**Кто использует:** Kolla pull и service containers.

**Команда:**

```bash
cd /opt/kolla-ansible
tools/powerops publish-images \
  --configdir "$PWD/etc/kolla" \
  --confirm-registry registry.example.invalid:5000
```

**Ожидаемый результат:** четыре push завершаются успешно; ни один tag не
перенаправлен в другой registry.

**Безопасный повтор:** повторный push того же content обновляет тот же tag;
для production предпочтительны immutable tags.

**Откат:** вернуть предыдущие runtime image references; не удалять registry
tags до завершения rollback.

**Статус:** NOT RUN IN THIS WORKSPACE: private registry недоступен.

## Kolla-Ansible prechecks

### Шаг 10. Bootstrap remote hosts

**Где:** deployment host; воздействие на все inventory hosts.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** globals, passwords и inventory.

**Что делает:** готовит container runtime, users и host prerequisites.

**Куда попадает:** remote control и compute operating systems.

**Кто использует:** последующий Kolla deploy.

**Команда:**

```bash
cd /opt/kolla-ansible
kolla-ansible bootstrap-servers --configdir "$PWD/etc/kolla" \
  -i "$PWD/etc/kolla/inventory"
```

**Ожидаемый результат:** play recap без failed hosts.

**Безопасный повтор:** Kolla bootstrap roles рассчитаны на повторный запуск.

**Откат:** зависит от host baseline; автоматического destructive rollback нет.

**Статус:** NOT RUN IN THIS WORKSPACE: target hosts не авторизованы.

### Шаг 11. Выполнить Kolla prechecks

**Где:** deployment host; read/validation access к inventory hosts и registry.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** все reviewed Kolla configs.

**Что делает:** проверяет network interfaces, ports, service prerequisites и
image access до изменения services.

**Куда попадает:** только Ansible output.

**Кто использует:** оператор change window.

**Команда:**

```bash
cd /opt/kolla-ansible
kolla-ansible prechecks --configdir "$PWD/etc/kolla" \
  -i "$PWD/etc/kolla/inventory"
```

**Ожидаемый результат:** recap без failed; PowerOps validator уже имеет status
`passed`.

**Безопасный повтор:** prechecks не выполняет PowerOps API mutations.

**Откат:** не требуется.

**Статус:** NOT RUN IN THIS WORKSPACE: target hosts не авторизованы.

## Deploy и reconfigure

`ansible/site.yml` импортирует `powerops.yml` после стандартных Ironic,
Mistral и Masakari plays. Роль запускает mutations только для
`kolla_action in ['deploy', 'reconfigure']`. Для stop, destroy, precheck и
upgrade PowerOps deletion path отсутствует.

Порядок внутри роли: preflight service flags и container entry points,
Ironic Node/Port reconciliation, Masakari segment/hosts, Mistral action
populate и workbook, redacted summary.

### Шаг 12. Выполнить deploy

**Где:** deployment host; production-impacting change window.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** весь checkout, globals, passwords и inventory.

**Что делает:** разворачивает OpenStack services и затем записывает PowerOps
records через OpenStack APIs.

**Куда попадает:** containers на controls/computes и Ironic, Masakari,
Mistral databases.

**Кто использует:** Ironic conductors, Masakari engine, Mistral processes.

**Команда:**

```bash
cd /opt/kolla-ansible
kolla-ansible deploy --configdir "$PWD/etc/kolla" \
  -i "$PWD/etc/kolla/inventory"
```

**Ожидаемый результат:** три Ironic API/conductor instances, два manageable
Nodes, один Masakari segment и workbook `power_ops`; summary не содержит BMC
secrets.

**Безопасный повтор:** matching records обновляются по exact name/MAC; records,
которых больше нет в globals, только report-ятся и не удаляются.

**Откат:** применить процедуру главы Rollback; автоматическое удаление records
не выполняется.

**Статус:** NOT RUN IN THIS WORKSPACE: target deployment не авторизован.

### Шаг 13. Выполнить idempotent reconfigure

**Где:** deployment host; production-impacting change window.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** изменённые globals, overrides, images или workbook.

**Что делает:** обновляет Kolla configs/containers и повторяет PowerOps
reconciliation из той же точки управления.

**Куда попадает:** соответствующие config volumes, containers и API records.

**Кто использует:** все PowerOps components.

**Команда:**

```bash
cd /opt/kolla-ansible
kolla-ansible reconfigure --configdir "$PWD/etc/kolla" \
  -i "$PWD/etc/kolla/inventory"
```

**Ожидаемый результат:** нет duplicate Nodes, Ports, segments, hosts или
workbooks. Маскированные BMC passwords могут безопасно применяться повторно и
помечать Node task changed.

**Безопасный повтор:** API records сопоставляются по exact identifiers; delete
не вызывается.

**Откат:** вернуть reviewed files/image tags и повторить reconfigure.

**Статус:** NOT RUN IN THIS WORKSPACE: target deployment не авторизован.

## Ironic enrollment и HA validation

Все проверки ниже выполняются только после успешного deploy. Сначала загрузить
admin credentials, созданные штатным Kolla post-deploy workflow. Nodes должны
оставаться `manageable`; переходы `available` и `active` являются ошибкой
границы решения.

### Шаг 14. Проверить conductors, Nodes и Ports

**Где:** deployment host; read-only OpenStack API.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** OpenStack API records.

**Что делает:** проверяет HA conductor membership и exact mappings.

**Куда попадает:** только stdout.

**Кто использует:** оператор acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
source "$PWD/etc/kolla/admin-openrc.sh"
openstack baremetal conductor list
openstack baremetal node list --long
openstack baremetal node show compute-01
node_uuid=$(openstack baremetal node show compute-01 -f value -c uuid)
openstack baremetal port list --node "$node_uuid"
```

**Ожидаемый результат:** три conductors с heartbeat; Node driver `redfish` или
`ipmi`, provision state `manageable`, network interface `noop`; MAC уникален.

**Безопасный повтор:** read-only.

**Откат:** не требуется.

**Статус:** NOT RUN IN THIS WORKSPACE: OpenStack API недоступен.

### Шаг 15. Проверить failover Ironic API

**Где:** deployment host, один выбранный controller; blast radius ограничен
одной API replica.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** inventory и running container state.

**Что делает:** останавливает одну API replica и проверяет VIP continuity.

**Куда попадает:** временно меняется container state на `controller-01`.

**Кто использует:** HA acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
ansible -i "$PWD/etc/kolla/inventory" controller-01 -b \
  -m ansible.builtin.command -a "podman stop ironic_api"
openstack baremetal node list
ansible -i "$PWD/etc/kolla/inventory" controller-01 -b \
  -m ansible.builtin.command -a "podman start ironic_api"
```

**Ожидаемый результат:** API list работает через VIP при остановленной replica;
после start healthcheck становится healthy.

**Безопасный повтор:** только после восстановления первой replica и проверки
quorum остальных services.

**Откат:** немедленно выполнить `podman start ironic_api` через Ansible.

**Статус:** NOT RUN IN THIS WORKSPACE: controller access не авторизован.

### Шаг 16. Проверить failover Ironic conductor

**Где:** deployment host и `controller-01`; blast radius одна conductor replica.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** conductor hash ring и Node state.

**Что делает:** останавливает один conductor, наблюдает takeover и не меняет
provision state.

**Куда попадает:** временно меняется container state на одном controller.

**Кто использует:** Ironic HA acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
before_state=$(openstack baremetal node show compute-01 -f value -c provision_state)
ansible -i "$PWD/etc/kolla/inventory" controller-01 -b \
  -m ansible.builtin.command -a "podman stop ironic_conductor"
openstack baremetal conductor list
openstack baremetal node power state compute-01
after_state=$(openstack baremetal node show compute-01 -f value -c provision_state)
test "$before_state" = "$after_state"
ansible -i "$PWD/etc/kolla/inventory" controller-01 -b \
  -m ansible.builtin.command -a "podman start ironic_conductor"
```

**Ожидаемый результат:** оставшиеся conductors обслуживают Node; state остаётся
`manageable`; после start возвращаются три heartbeat entries.

**Безопасный повтор:** не останавливать второй conductor, пока первый не healthy.

**Откат:** запустить `ironic_conductor` на выбранном controller.

**Статус:** NOT RUN IN THIS WORKSPACE: conductor failover не авторизован.

### Шаг 17. Проверить Redfish power на пустом maintenance host

**Где:** deployment host; blast radius один физический test host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** Node `compute-01`, Nova inventory и Redfish BMC.

**Что делает:** после доказательства отсутствия instances выполняет status,
off и on через Ironic.

**Куда попадает:** физическое power state test host.

**Кто использует:** Ironic Redfish acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
test_host=compute-01
openstack compute service set --disable --disable-reason powerops-test "$test_host" nova-compute
test "$(openstack server list --all-projects --host "$test_host" -f value -c ID | wc -l | tr -d ' ')" = "0"
openstack baremetal node power state "$test_host"
openstack baremetal node power off "$test_host"
openstack baremetal node power state "$test_host"
openstack baremetal node power on "$test_host"
openstack baremetal node power state "$test_host"
```

**Ожидаемый результат:** consecutive observations подтверждают off/on; Node
остаётся `manageable`; instances не мигрируют и не evacuate-ятся.

**Безопасный повтор:** только после подтверждения empty host и завершения
предыдущего power transition.

**Откат:** power on через Ironic; Nova service не включать до проверки stale
domains.

**Статус:** NOT RUN IN THIS WORKSPACE: BMC operation не авторизована.

### Шаг 18. Проверить IPMI fallback

**Где:** deployment host; blast radius `compute-02` с совместимым IPMI BMC.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** Node `compute-02` и IPMI credentials из globals.

**Что делает:** повторяет безопасный power-cycle contract через `ipmitool`
management/power interface Ironic.

**Куда попадает:** physical state одного IPMI host.

**Кто использует:** Ironic IPMI acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
test_host=compute-02
openstack compute service set --disable --disable-reason powerops-test "$test_host" nova-compute
test "$(openstack server list --all-projects --host "$test_host" -f value -c ID | wc -l | tr -d ' ')" = "0"
openstack baremetal node power off "$test_host"
openstack baremetal node power on "$test_host"
openstack baremetal node power state "$test_host"
```

**Ожидаемый результат:** final state `power on`, Node `manageable`.

**Безопасный повтор:** только на empty test host.

**Откат:** power on и ручная проверка console/host health.

**Статус:** NOT RUN IN THIS WORKSPACE: IPMI operation не авторизована.

## Masakari emergency fencing validation

Пример globals создаёт hosts с `masakari_on_maintenance: true`, поэтому
автоматическое emergency recovery безопасно выключено на этапе commissioning.
Для acceptance одного test host изменить только его значение на `false` и
выполнить reviewed reconfigure. Не включать auto recovery сразу на всех узлах.

### Шаг 19. Проверить segment и host records

**Где:** deployment host; read-only Masakari API.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** Masakari segment/host records.

**Что делает:** проверяет recovery method и maintenance flags.

**Куда попадает:** только stdout.

**Кто использует:** Masakari acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
openstack segment list
segment_uuid=$(openstack segment list -f value -c uuid -c name | awk '$2=="powerops-compute" {print $1}')
openstack segment show "$segment_uuid"
openstack segment host list "$segment_uuid"
openstack notification list
```

**Ожидаемый результат:** ровно один `powerops-compute`, recovery `auto`, два
unique hosts и reviewed `on_maintenance` values.

**Безопасный повтор:** read-only.

**Откат:** не требуется.

**Статус:** NOT RUN IN THIS WORKSPACE: Masakari API недоступен.

### Шаг 20. Проверить fence-before-evacuate на одном test host

**Где:** изолированная acceptance среда; blast radius test host и его HA VMs.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** healthy BMC mapping, host monitor и Masakari flow.

**Что делает:** создаёт controlled `COMPUTE_HOST` failure notification;
Masakari отключает Nova service, Ironic подтверждает stable physical off и
только затем запускает preparation/evacuation.

**Куда попадает:** Masakari notifications/VMoves, Nova placement и physical
power state.

**Кто использует:** emergency recovery acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
test_host=compute-01
generated_time=$(date -u +%Y-%m-%dT%H:%M:%S.000000)
openstack notification create \
  --type COMPUTE_HOST \
  --hostname "$test_host" \
  --generated-time "$generated_time" \
  --payload '{"event":"STOPPED","cluster_status":"OFFLINE"}'
openstack notification list
notification_uuid=$(openstack notification list -f value -c notification_uuid | head -n 1)
openstack notification show "$notification_uuid"
openstack server list --all-projects --host "$test_host" --long
```

**Ожидаемый результат:** notification заканчивается `finished`; logs содержат
fencing confirmation до `prepare_HA_enabled_instances_task` и
`evacuate_instances_task`; VMs больше не размещены на source host.

**Безопасный повтор:** использовать новую notification только после закрытия
предыдущей и восстановления host.

**Откат:** power on через Ironic, проверить stale domains, вернуть compute и
Masakari host в reviewed state.

**Статус:** NOT RUN IN THIS WORKSPACE: emergency evacuation не авторизована.

### Шаг 21. Доказать fail-closed при BMC timeout

**Где:** только изолированная acceptance среда; blast radius один test host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** отдельный test BMC credential и Masakari flow.

**Что делает:** после backup временно задаёт заведомо нерабочий test credential
в globals, выполняет reconfigure и повторяет controlled notification. Role
переотправляет masked BMC secret в Ironic.

**Куда попадает:** временно меняется Ironic driver_info test Node и создаётся
failed Masakari notification.

**Кто использует:** fail-closed acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
cp "$PWD/etc/kolla/globals.yml" "$PWD/etc/kolla/globals.yml.bmc-test-backup"
kolla-ansible reconfigure --configdir "$PWD/etc/kolla" \
  -i "$PWD/etc/kolla/inventory"
openstack notification list
openstack server list --all-projects --host compute-01 --long
```

**Ожидаемый результат:** notification имеет error с redacted fencing failure;
VMs остаются на source host, `evacuate_instances_task` не выполняется.

**Безопасный повтор:** не повторять до восстановления correct credential.

**Откат:** восстановить backup globals, mode `0600`, выполнить reconfigure и
проверить Ironic power validation.

**Статус:** NOT RUN IN THIS WORKSPACE: destructive failure injection не
авторизован.

## Mistral planned workflow validation

### Что делает Mistral в этой архитектуре

Mistral является оркестратором только плановых операций с compute host. Он
принимает запрос оператора через OpenStack Workflow API, читает workbook
`power_ops`, строит последовательность tasks и передаёт custom actions в
`mistral_executor`. Actions обращаются к Nova, Masakari и Ironic через их API;
отдельные скрипты на compute host не копируются и не запускаются.

Компоненты Mistral участвуют следующим образом:

| Компонент | Роль в PowerOps |
|---|---|
| `mistral_api` | принимает запуск workflow и отдаёт состояние executions/tasks |
| `mistral_engine` | читает workbook, вычисляет переходы `on-success`/`on-error`, хранит execution state и выполняет discovery actions через `mistral-db-manage populate` |
| `mistral_executor` | загружает `powerops.*` entry points из wheel и выполняет OpenStack API operations |
| Redis Sentinel | хранит owner-safe lock `powerops:host:<hostname>`, исключающий две одновременные плановые операции над одним host |

Workbook предоставляет четыре workflow:

| Workflow | Назначение | Изменяет состояние |
|---|---|---|
| `power_ops.host_power_status` | возвращает Nova service status/state, текущий и целевой Ironic power state и Masakari maintenance flag | нет, read-only |
| `power_ops.planned_power_off` | безопасно выводит host из планирования, обрабатывает instances и выключает его через Ironic | да |
| `power_ops.planned_reboot` | выполняет controlled power off/on и возвращает проверенный host в scheduler | да |
| `power_ops.power_on_and_return` | включает ранее выключенный host, проверяет его возврат и только затем разрешает scheduling | да |

`planned_power_off` выполняется в следующем порядке:

```text
получить Redis lock
  -> найти ровно по одному Nova service, Ironic Node и Masakari host
  -> включить Masakari maintenance
  -> disable nova-compute
  -> обработать instances по выбранной policy
  -> обновить Redis lock
  -> доказать, что host безопасен для выключения
  -> запросить soft power off через Ironic
  -> дождаться стабильного power off
  -> записать audit event
  -> освободить Redis lock
```

После успешного `planned_power_off` Nova service остаётся disabled, а
Masakari host — на maintenance. Это ожидаемое состояние выключенного узла, а
не незавершённый rollback.

Допустимы три политики обработки instances:

- `require_empty` — немедленно завершить workflow ошибкой, если на host есть
  хотя бы один server;
- `live_migrate` — запросить live migration всех servers и ждать, пока они
  покинут source host;
- `stop` — остановить servers и разрешить выключение, только если все они
  достигли `SHUTOFF`.

Политики `evacuate` здесь нет. Evacuation относится к аварийному Masakari
TaskFlow и запускается только после `ironic_fence`. Плановый Mistral workflow
не создаёт Masakari failure notification и не вызывает evacuation.

`planned_reboot` сначала выполняет те же drain и power-off gates, затем
включает host через Ironic, ждёт `nova-compute state=up`, проверяет возврат
host и только после этого включает Nova service и снимает Masakari
maintenance. `power_on_and_return` выполняет только часть включения и возврата
для host, который уже был безопасно выключен.

Перед возвратом host требуется `stale_domains_checked=true`. Сам Mistral не
заходит по SSH и не выполняет `virsh`: оператор обязан отдельно проверить
отсутствие stale libvirt domains на compute host и только потом передать этот
флаг. Передача `true` без фактической проверки обходит организационный safety
gate. Дополнительно проверяются Ironic `power on`, Nova service `up` и, если
они перечислены в конфигурации, обязательные Neutron agents.

Любая ожидаемая ошибка переводит workflow в fail-safe path: Nova service
остаётся disabled, Masakari maintenance остаётся включённым, записывается
failure audit event и выполняется owner-safe освобождение Redis lock. Другой
workflow не может обновить или удалить lock, если его owner не совпадает с ID
текущего Mistral execution.

Плановые workflows используют owner-safe Redis lock, Masakari maintenance,
Nova disable/drain, Ironic power и stable polling. Политики instances:
`require_empty`, `live_migrate`, `stop`. Значение `evacuate` намеренно
отклоняется. Перед возвратом scheduler workflow требует внешнюю проверку stale
libvirt domains.

### Шаг 22. Проверить actions, workbook и read-only status workflow

**Где:** deployment host; сначала read-only Mistral API.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** populated actions и workbook `power_ops`.

**Что делает:** проверяет discovery и запускает status workflow.

**Куда попадает:** Mistral execution/task records; host state не меняется.

**Кто использует:** оператор planned workflow acceptance.

**Команда:**

```bash
cd /opt/kolla-ansible
openstack workbook list
openstack workflow list
openstack action definition list | grep '^powerops\.'
openstack workflow execution create \
  power_ops.host_power_status '{"host":"compute-01"}'
execution_id=$(openstack workflow execution list -f value -c ID | head -n 1)
openstack workflow execution show "$execution_id"
openstack task execution list --workflow-execution "$execution_id"
```

**Ожидаемый результат:** 15 `powerops.*` actions, четыре workflows и successful
status execution с Ironic/Nova/Masakari identifiers.

**Безопасный повтор:** status workflow read-only.

**Откат:** не требуется; execution history не удаляется автоматически.

**Статус:** NOT RUN IN THIS WORKSPACE: Mistral API недоступен.

### Шаг 23. Проверить planned power off

**Где:** deployment host; blast radius один empty или drainable compute host.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** workflow `power_ops.planned_power_off` и explicit policy.

**Что делает:** блокирует concurrent operation, ставит Masakari maintenance,
отключает Nova scheduler, применяет policy, подтверждает host-safe и power off.

**Куда попадает:** Redis lock, Nova/Masakari state, Mistral execution и physical
power state.

**Кто использует:** оператор maintenance window.

**Команда:**

```bash
cd /opt/kolla-ansible
openstack workflow execution create \
  power_ops.planned_power_off \
  '{"host":"compute-01","instance_policy":"require_empty"}'
execution_id=$(openstack workflow execution list -f value -c ID | head -n 1)
openstack workflow execution show "$execution_id"
openstack task execution list --workflow-execution "$execution_id"
```

**Ожидаемый результат:** success только после empty check и stable power off;
при ошибке Nova остаётся disabled, Masakari maintenance остаётся true.

**Безопасный повтор:** второй concurrent execution отклоняется Redis lock.

**Откат:** выполнить controlled return workflow после проверки host hardware.

**Статус:** NOT RUN IN THIS WORKSPACE: planned power operation не авторизована.

### Шаг 24. Проверить stale domains и вернуть host

**Где:** deployment host и выключавшийся compute; blast radius возврат одного
host в scheduler.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** Nova placement и host-local libvirt domain list.

**Что делает:** сравнивает фактические domains с Nova и только после review
передаёт `stale_domains_checked=true`.

**Куда попадает:** Mistral execution, physical on, Nova service enabled и
Masakari maintenance false.

**Кто использует:** return-to-service gate.

**Команда:**

```bash
cd /opt/kolla-ansible
openstack server list --all-projects --host compute-01 --long
ansible -i "$PWD/etc/kolla/inventory" compute-01 -b \
  -m ansible.builtin.command -a "virsh list --all --name"
openstack workflow execution create \
  power_ops.power_on_and_return \
  '{"host":"compute-01","stale_domains_checked":true}'
execution_id=$(openstack workflow execution list -f value -c ID | head -n 1)
openstack workflow execution show "$execution_id"
```

**Ожидаемый результат:** workflow ждёт power on и Nova heartbeat, затем
снимает maintenance и включает scheduler. Без true gate операция завершается
ошибкой до enable.

**Безопасный повтор:** status сначала подтвердит уже возвращённое состояние;
owner lock не допускает concurrent mutation.

**Откат:** disable Nova service и включить Masakari maintenance, если post-check
выявил stale domain или hardware error.

**Статус:** NOT RUN IN THIS WORKSPACE: compute/libvirt access не авторизован.

## Диагностика

### Шаг 25. Собрать redacted диагностику из одной точки

**Где:** deployment host; read-only API и container logs.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** validation report, Ansible inventory, service logs.

**Что делает:** проверяет source contracts и собирает состояния без печати
globals/passwords.

**Куда попадает:** stdout и существующий redacted report.

**Кто использует:** дежурный инженер.

**Команда:**

```bash
cd /opt/kolla-ansible
tools/powerops validate \
  --configdir "$PWD/etc/kolla" \
  --inventory "$PWD/etc/kolla/inventory" \
  --report "$PWD/reports/powerops-validation.json"
openstack baremetal conductor list
openstack baremetal node list --long
openstack segment list
openstack notification list
openstack workbook list
openstack workflow execution list
ansible -i "$PWD/etc/kolla/inventory" control -b \
  -m ansible.builtin.command -a "podman ps --format {{.Names}}:{{.Status}}"
```

**Ожидаемый результат:** local validation passed; services healthy; no
credential appears in report or command output.

**Безопасный повтор:** read-only, кроме перезаписи redacted report.

**Откат:** не требуется.

Типовые причины:

| Симптом | Проверка | Безопасное действие |
|---|---|---|
| `ironic_fence entry point missing` | derived Masakari image и `pip show` внутри container | вернуть correct image tag, reconfigure |
| `powerops.* action missing` | три derived Mistral images и `mistral-db-manage populate` | исправить images, reconfigure |
| duplicate Node или MAC | exact Ironic lists и globals | исправить mapping; лишнюю запись не удалять автоматически |
| Node не `manageable` | `node show` и conductor logs | остановиться; роль не меняет forbidden state |
| fencing timeout | BMC routing/TLS/credential, `last_error` | оставить Nova disabled; восстановить BMC, повторить отдельную notification |
| Redis lock lost | Sentinel quorum и Mistral executor logs | не запускать второй workflow; восстановить Sentinel |
| return gate failed | Nova heartbeat и host-local `virsh` | оставить host disabled/maintenance |

## Rollback и decommission

Rollback кода и containers не означает deletion API records. Роль намеренно
не удаляет Ironic Nodes/Ports, Masakari segments/hosts, Mistral workbooks,
executions или notifications.

### Шаг 26. Выполнить недеструктивный rollback runtime

**Где:** deployment host; production-impacting change window.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** защищённая копия предыдущих globals/overrides и прежние image
tags.

**Что делает:** одновременно восстанавливает previous image references и
Masakari flow, чтобы `ironic_fence` не ссылался на отсутствующий plugin, затем
reconfigure services.

**Куда попадает:** Kolla config volumes и service containers.

**Кто использует:** Kolla rollback.

**Команда:**

```bash
cd /opt/kolla-ansible
chmod 0600 "$PWD/etc/kolla/globals.yml"
kolla-ansible reconfigure --configdir "$PWD/etc/kolla" \
  -i "$PWD/etc/kolla/inventory"
```

**Ожидаемый результат:** services используют прежние images/config; OpenStack
records сохранены для расследования и отдельного решения.

**Безопасный повтор:** reconfigure повторяем после проверки exact files.

**Откат:** если rollback ухудшил состояние, вернуть PowerOps image/config pair
как единый согласованный набор и повторить reconfigure.

**Статус:** NOT RUN IN THIS WORKSPACE: target runtime отсутствует.

### Шаг 27. Подготовить decommission без неявного удаления

**Где:** deployment host; сначала строго read-only discovery.

**Текущий каталог:** `/opt/kolla-ansible`.

**Входной файл:** текущие OpenStack records и approved decommission ticket.

**Что делает:** перечисляет объекты и зависимости; ничего не удаляет.

**Куда попадает:** stdout и change record оператора.

**Кто использует:** владелец Ironic/Masakari/Mistral и change approver.

**Команда:**

```bash
cd /opt/kolla-ansible
openstack baremetal node list --long
openstack baremetal port list
openstack segment list
openstack segment host list "$segment_uuid"
openstack workbook list
openstack workflow execution list
```

**Ожидаемый результат:** полный перечень Node/Port, segment/host, workbook и
execution dependencies.

**Безопасный повтор:** read-only.

**Откат:** не требуется.

Удаление выполняется только отдельной, явно одобренной процедурой после
проверки отсутствия VMs, notifications, host membership и audit retention.
`tools/powerops`, роль и rollback не содержат delete path.

## Проверка целостности вложенных схем

Исходные и вложенные файлы проверены побайтно:

```text
e40547ec39a98cf180d2f3555365d5536bd96b4fb0043344d5b32450dd1d2b3a  ironic-ha-power-workflows.svg
8c214c5e3a210814e6f469a1e331a7e2efadbeb96aa99e2d47201c24e741fea2  ironic-ha-power-workflows.png
```

SVG является редактируемым источником. PNG предназначен для быстрого просмотра
и включения в документы. ZIP из корня workspace в подготовке решения не
использовался и не читался.
