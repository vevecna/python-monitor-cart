# Monitor de Estoque

Monitora a página de um produto em uma loja online, avisa quando ele fica disponível e,
opcionalmente, adiciona ao carrinho da sua conta. Você finaliza a compra manualmente.

Uso pessoal, um produto por vez. Não automatiza checkout nem pagamento.

## Como funciona

A cada intervalo o script abre a página com o Chromium (via Playwright) e classifica o
resultado em três estados:

| Estado | Quando | O que acontece |
|---|---|---|
| `INDISPONIVEL` | a frase de esgotado está **visível** | espera o próximo ciclo |
| `DISPONIVEL` | a frase **não** está visível **e** o botão está visível **e** habilitado | notifica uma vez, clica se pedido, e encerra |
| `DESCONHECIDO` | timeout, HTTP não-2xx, CAPTCHA, layout mudou | espera com backoff exponencial |

A regra central: **ausência da frase não é prova de disponibilidade**. Só um botão
visível e clicável confirma. Qualquer ambiguidade vira `DESCONHECIDO`, nunca `DISPONIVEL`
— um falso positivo faria você correr para uma página que não tem o produto.

O navegador roda com um perfil persistente em `./perfil_navegador/`, então a sessão da
loja sobrevive entre execuções e o item cai no carrinho da **sua** conta.

## Estrutura

```
monitor/
├── config.py         Config (imutável) e leitura do .env
├── notificadores.py  Notificador (ABC), Console, Telegram, montar_notificadores()
├── verificador.py    Estado (enum) e VerificadorEstoque: carrega e classifica
├── monitor.py        Monitor: loop, backoff e disparo das notificações
└── __main__.py       CLI, logging e composição das peças
```

As dependências apontam numa direção só: `__main__` → `monitor` → {`verificador`,
`notificadores`} → `config`. Nenhum módulo importa quem o usa, então dá para importar o
pacote como biblioteca sem arrastar o argparse junto.

## Instalação

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Uso

### 1. Descubra o seletor e a frase da loja

Cada loja usa uma marcação diferente, e os padrões do script quase certamente não servem
para a sua. Abra a página do produto, aperte `F12` e localize:

- **o botão de carrinho** — prefira `id` ou `data-testid` a texto, porque texto muda com
  promoção e idioma: `#btn-comprar`, `[data-testid='add-to-cart']`;
- **a frase de esgotado** — abra a página de um produto que você sabe estar fora de
  estoque e copie o texto exato.

Duas armadilhas que aparecem quase sempre, ilustradas pelo exemplo real logo abaixo:

- **o botão pode não ser um `<button>`.** Muitas lojas usam `<a>` ou `<div>`, e aí o
  seletor padrão do script encontra zero elementos;
- **a frase precisa estar de fato visível.** É comum a loja deixar o texto de esgotado no
  HTML o tempo todo e apenas escondê-lo com CSS. Uma frase assim nunca casa, porque o
  script exige visibilidade — confira no F12 se o elemento está realmente sendo exibido.

#### Exemplo verificado: Copag Loja (plataforma VTEX)

```bash
--seletor "a.buy-button-ref" --frase "AVISE-ME"
```

| Campo | Valor | Por quê |
|---|---|---|
| botão | `a.buy-button-ref` | é um `<a href="/checkout/cart/add?...">`, não um `<button>` |
| frase | `AVISE-ME` | "Produto Esgotado" existe no DOM, mas **oculto**; o widget "AVISE-ME" é o que aparece |

Conferido nas duas pontas: um produto em estoque classifica como `DISPONIVEL` e um
esgotado como `INDISPONIVEL`. Observações desta loja:

- clicar navega para o carrinho, porque o botão é um link — a janela sai da página do
  produto, e isso é esperado;
- há um banner de cookies sobrepondo parte da tela; aceitá-lo uma vez durante o `--login`
  deixa a escolha salva no perfil e evita que ele interfira no clique.

### 2. Faça login uma vez

```bash
python -m monitor "https://www.loja.com.br/produto/123" --login
```

Abre uma janela, você se autentica, pressiona Enter no terminal. A sessão fica salva.

### 3. Valide com a janela aberta

```bash
python -m monitor "https://www.loja.com.br/produto/123" \
  --seletor "[data-testid='add-to-cart']" \
  --frase "Produto indisponível" \
  --visivel
```

Confira nos logs se o estado bate com o que você vê na tela. Só depois disso deixe rodando.

### 4. Monitore

```bash
python -m monitor "https://www.loja.com.br/produto/123" \
  --seletor "[data-testid='add-to-cart']" \
  --frase "Produto indisponível" \
  --clicar --intervalo 300
```

Sempre entre aspas: URLs contêm `&` e `?`, que o shell interpreta.

### Opções

| Flag | Padrão | Descrição |
|---|---|---|
| `url` | — | obrigatória, posicional |
| `--seletor` | `button:has-text('Adicionar ao carrinho')` | seletor CSS do botão |
| `--frase` | `Este produto não está disponível no momento` | texto de esgotado |
| `--intervalo` | `120` | segundos entre verificações (mínimo **60**) |
| `--clicar` | desligado | adiciona ao carrinho ao detectar |
| `--visivel` | desligado | abre o navegador com janela |
| `--login` | desligado | autentica e sai |
| `--perfil` | `./perfil_navegador` | diretório da sessão |
| `--testar-notificacao` | desligado | manda uma mensagem de teste e sai; dispensa a URL |

### Notificação no Telegram (opcional)

Sem isso, a notificação é só o beep e a mensagem no terminal.

**1. Crie o bot.** No Telegram, fale com [@BotFather](https://t.me/BotFather), envie
`/newbot` e siga as perguntas. Ele devolve um token no formato `123456:ABC-DEF...`.

**2. Mande uma mensagem para o seu próprio bot.** Abra a conversa com ele e envie
qualquer coisa, um `/start` serve. Sem esse primeiro contato **o bot não tem permissão
para te escrever** — é a causa mais comum de "configurei e não chega nada".

**3. Descubra o seu chat id:**

```bash
curl -s "https://api.telegram.org/bot<SEU_TOKEN>/getUpdates" | grep -o '"id":[0-9-]*' | head -1
```

**4. Guarde as credenciais e teste.** Crie um arquivo `.env` na raiz do projeto — ele já
está no `.gitignore`:

```bash
cat > .env <<'EOF'
TELEGRAM_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=987654321
EOF
chmod 600 .env

python -m monitor --testar-notificacao
```

O script lê o `.env` sozinho, sem dependência extra. Regras do formato:

- `CHAVE=VALOR`, uma por linha; `export ` na frente é aceito e ignorado;
- aspas em volta do valor são removidas;
- `#` só inicia comentário **no começo da linha** — um `#` no meio do valor é preservado,
  porque tokens podem contê-lo;
- **variável já exportada no ambiente vence o arquivo**, o que permite sobrescrever numa
  execução só: `TELEGRAM_CHAT_ID=outro python -m monitor --testar-notificacao`.

Se preferir não usar arquivo, `export TELEGRAM_TOKEN=...` no terminal funciona igual.

O modo de teste não abre o navegador e não precisa de URL: manda uma mensagem pelos
canais configurados e sai. Se algo estiver errado, o log diz o quê:

| Log | Causa |
|---|---|
| `Só o console está ativo` | nenhuma das duas variáveis foi exportada |
| `falta a variável TELEGRAM_CHAT_ID` | typo no nome da variável, ou exportou só uma |
| `HTTP 401: Unauthorized` | token errado |
| `HTTP 400: chat not found` | chat id errado |
| `HTTP 403: bot can't initiate conversation` | você não fez o passo 2 |

O token nunca aparece no log. Em falha de rede o script registra só o tipo da exceção,
porque a mensagem do `requests` carrega a URL completa com o token; nas recusas ele mostra
a descrição vinda do corpo da resposta, que é segura.

## Deixar rodando em segundo plano

O monitor pode levar horas ou dias até o produto voltar, então você não vai querer um
terminal preso. Três formas, da mais simples à mais robusta.

### tmux — para acompanhar de perto

```bash
tmux new -s monitor
python -m monitor "URL" --seletor "..." --frase "..." --clicar --intervalo 300
# Ctrl+B depois D para destacar; o processo continua
tmux attach -t monitor    # voltar a ver
```

### nohup — para esquecer

```bash
nohup python -m monitor "URL" --seletor "..." --frase "..." --clicar --intervalo 300 \
  > monitor.log 2>&1 &
tail -f monitor.log
```

### systemd — para sobreviver a reinícios

Há uma unidade pronta em [`deploy/monitor.service`](deploy/monitor.service):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/monitor.service ~/.config/systemd/user/
# edite o ExecStart com a sua URL, seletor e frase
systemctl --user daemon-reload
systemctl --user start monitor
journalctl --user -u monitor -f      # acompanhar
systemctl --user stop monitor        # parar
```

Para vários produtos, copie o arquivo com outro nome e ajuste o `ExecStart` de cada um.

Duas coisas importantes na unidade:

- **`Restart=on-failure`, nunca `Restart=always`.** O monitor encerra de propósito quando
  acha o produto. Com `always`, o systemd o reiniciaria e ele adicionaria o item ao
  carrinho de novo, em loop.
- **`WorkingDirectory` no diretório do projeto**, senão o `.env` e o `./perfil_navegador`
  não são encontrados e o serviço roda sem sessão e sem Telegram.

O serviço termina com `Result=success` quando encontra o produto. Confira com
`systemctl --user status monitor`: `inactive (dead)` depois de uma notificação é o
resultado esperado, não uma falha.

### Se você usa WSL

Um processo em segundo plano no Linux só vive enquanto a WSL viver, e ela é desligada
quando você fecha a última janela. Para o monitor sobreviver:

```bash
loginctl enable-linger $USER    # serviços de usuário continuam após o logout
```

E, no Windows, em `%UserProfile%\.wslconfig`:

```ini
[wsl2]
vmIdleTimeout=-1
```

Sem isso, fechar todos os terminais derruba o monitor junto — independentemente de estar
em tmux, nohup ou systemd.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes montam HTML local com `page.set_content(...)`; nenhum toca a loja real.

## Limites deliberados

Estas ausências são escolhas, não pendências:

- não automatiza checkout, pagamento, dados de cartão nem 2FA;
- não contorna CAPTCHA, não rotaciona proxies, não falsifica fingerprint;
- não aceita intervalo abaixo de 60 s e não paraleliza requisições à mesma loja;
- notifica uma vez e encerra, em vez de ficar repetindo.

Respeite os Termos de Serviço da loja. Muitos proíbem automação, e a conta é sua.

## Ideias futuras

- ler também `--seletor`/`--frase` do `.env`, em vez de repetir flags longas;
- várias URLs em uma execução (uma aba por produto, mesmo intervalo mínimo);
- outros notificadores: Discord, e-mail SMTP, ntfy.sh — basta uma subclasse de
  `Notificador` registrada em `montar_notificadores()`;
- screenshot anexado à notificação quando disponível ou após falhas repetidas.
