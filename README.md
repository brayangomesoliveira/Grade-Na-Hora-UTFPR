<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&height=220&text=Grade%20Na%20Hora%20UTFPR&fontSize=42&fontAlignY=38&desc=Monte%20sua%20grade%20com%20Turmas%20Abertas%20da%20UTFPR&descAlignY=60&color=0:00D4FF,35:2563EB,70:7C3AED,100:FF5F6D&fontColor=ffffff" width="100%" />
</div>

<div align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-0ea5e9?style=for-the-badge&logo=python&logoColor=white">
  <img alt="PySide6" src="https://img.shields.io/badge/PySide6-Desktop-22c55e?style=for-the-badge&logo=qt&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/Playwright-Automacao-2563eb?style=for-the-badge&logo=playwright&logoColor=white">
  <img alt="Pillow" src="https://img.shields.io/badge/Pillow-Exportacao%20PNG-f97316?style=for-the-badge">
</div>

<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=rect&height=10&color=0:00D4FF,50:2563EB,100:FF5F6D&section=header" width="100%" />
</div>

## O que é

O Grade Na Hora UTFPR é um aplicativo desktop que ajuda você a:

- entrar no Portal do Aluno da UTFPR
- abrir a área de Turmas Abertas
- selecionar campus e curso
- carregar os horários das turmas
- montar sua grade
- detectar conflitos
- exportar a grade em imagem PNG

## Como funciona (fluxo real)

O aplicativo segue o fluxo real da UTFPR:

1. Abre a página pública do Portal do Aluno da UTFPR
2. Seleciona a cidade/campus
3. Faz login
4. Entra no Portal do Aluno
5. Abre Turmas Abertas
6. Se a UTFPR pedir, mostra uma janela de seleção de curso no próprio aplicativo
7. Carrega as turmas e horários do curso escolhido
8. Monta a grade e mostra conflitos

## O que o aplicativo faz automaticamente

- navega por telas com menu dinâmico (AJAX)
- trata páginas com iframe
- tenta caminhos alternativos quando o clique não responde
- detecta quando a página pede seleção de curso
- lê informações pelo conteúdo da página (código-fonte/HTML) quando isso é mais confiável
- mantém a interface responsiva enquanto carrega os dados

## Quando entra em Turmas Abertas e não acontece nada (possíveis erros)

Abaixo estão os problemas mais comuns nessa etapa:

### 1. O clique em Turmas Abertas aconteceu visualmente, mas não disparou a ação

Possíveis causas:
- menu ainda não terminou de carregar
- elemento estava com overlay por cima
- handler JavaScript ainda não estava pronto
- clique caiu no texto/caixa errada

O que já foi melhorado:
- validação de transição após clique
- tentativas por rota direta, clique normal, clique em iframe e clique por JavaScript

### 2. Turmas Abertas abriu, mas a página ficou esperando o curso

Possíveis causas:
- a página abriu a tela intermediária com seleção de curso
- o curso ainda não foi escolhido
- o botão de confirmar não foi acionado

O que já foi melhorado:
- popup de seleção de curso no aplicativo
- leitura dinâmica da lista de cursos da própria página

### 3. Turmas Abertas abriu dentro de iframe e o scraper ficou olhando a página principal

Possíveis causas:
- a tabela real está em iframe
- a tela de seleção de curso está em um frame diferente

O que já foi melhorado:
- busca em page e iframes
- validação da tabela/curso dentro de frame

### 4. Sessão expirou e voltou para login sem avisar

Possíveis causas:
- sessão antiga expirada
- tempo de inatividade
- redirecionamento silencioso

O que já foi melhorado:
- detecção de retorno para login
- estado de sessão expirada no fluxo

### 5. CAPTCHA ou 2FA apareceu

Possíveis causas:
- proteção do portal
- validação de segurança em login

Como o app trata:
- detecta o caso
- evita ficar em loop
- pede intervenção manual

### 6. A tabela de turmas não carrega, mas a tela está aberta

Possíveis causas:
- o portal está lento
- o botão Confirmar não foi processado
- o HTML mudou

O que já foi melhorado:
- retries com backoff
- leitura por HTML/código-fonte como fallback
- logs e artefatos de erro

## O que já foi melhorado no aplicativo

- seleção de curso em janela popup (mais claro para o usuário)
- leitura de cursos e turmas pelo HTML da página quando necessário
- matching tolerante para nomes de curso (variações de texto)
- retries com backoff para falhas transitórias
- state machine de navegação para reduzir travamentos e loops
- smoke test para validar o caminho até Turmas Abertas

## Como usar (simples)

1. Abra o aplicativo
2. Escolha sua cidade/campus
3. Digite RA e senha
4. Clique em Entrar
5. Se aparecer a janela de curso, selecione seu curso
6. Aguarde o carregamento das turmas
7. Selecione as turmas que você quer
8. Gere sua grade e exporte a imagem

## Segurança

- a senha fica somente em memória
- o aplicativo não salva senha em arquivo
- o estado salvo localmente guarda preferências e seleções

## Se algo der erro

Ative o modo debug no aplicativo e teste novamente.

O app salva evidências para análise:

- log principal
- screenshots de erro
- HTML da página no momento da falha

Isso ajuda muito quando a UTFPR muda algum detalhe do portal.

## Estrutura do projeto (resumo)

- src: código do aplicativo
- src/ui: interface desktop
- src/infra: scraper e integração com o portal
- src/core: modelos, parser, grade e exportação
- tests: testes automatizados
- logs: arquivos de depuração

## Status atual

O aplicativo já suporta:

- campus/cidade
- login
- abertura de Turmas Abertas
- seleção dinâmica de curso
- leitura de turmas e horários
- montagem de grade
- conflitos
- exportação PNG

<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=transparent&height=140&text=Portal%20UTFPR%20%20%E2%86%92%20%20Curso%20%20%E2%86%92%20%20Turmas%20%20%E2%86%92%20%20Grade&fontSize=28&fontAlignY=55&color=0:00D4FF,25:22C55E,50:F59E0B,75:EF4444,100:7C3AED&fontColor=ffffff" width="100%" />
  <img src="https://capsule-render.vercel.app/api?type=waving&height=120&section=footer&color=0:FF5F6D,30:F97316,60:2563EB,100:00D4FF" width="100%" />
</div>
