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

### 2. Faça login uma vez

```bash
python monitor_estoque.py "https://www.loja.com.br/produto/123" --login
```

Abre uma janela, você se autentica, pressiona Enter no terminal. A sessão fica salva.

### 3. Valide com a janela aberta

```bash
python monitor_estoque.py "https://www.loja.com.br/produto/123" \
  --seletor "[data-testid='add-to-cart']" \
  --frase "Produto indisponível" \
  --visivel
```

Confira nos logs se o estado bate com o que você vê na tela. Só depois disso deixe rodando.

### 4. Monitore

```bash
python monitor_estoque.py "https://www.loja.com.br/produto/123" \
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

**4. Exporte e teste:**

```bash
export TELEGRAM_TOKEN="123456:ABC-DEF..."
export TELEGRAM_CHAT_ID="987654321"

python monitor_estoque.py --testar-notificacao
```

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

- carregar configuração de `.env` ou TOML, em vez de repetir flags longas;
- várias URLs em uma execução (uma aba por produto, mesmo intervalo mínimo);
- outros notificadores: Discord, e-mail SMTP, ntfy.sh — basta uma subclasse de
  `Notificador` registrada em `montar_notificadores()`;
- screenshot anexado à notificação quando disponível ou após falhas repetidas.
