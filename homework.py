import logging
import os
import sys
import time
from http import HTTPStatus
from logging import StreamHandler
from typing import cast

import requests
import telegram
import telegram.ext
from dotenv import load_dotenv
from telegram import TelegramError

from exceptions import SendingError

load_dotenv()

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = StreamHandler(stream=sys.stdout)
logger.addHandler(handler)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
handler.setFormatter(formatter)


PRACTICUM_TOKEN: str = cast(str, os.getenv('PRACTICUM_TOKEN', 'default_value'))
TELEGRAM_TOKEN: str = cast(str, os.getenv('TELEGRAM_TOKEN', 'default_value'))
TELEGRAM_CHAT_ID: str = cast(str, os.getenv(
    'TELEGRAM_CHAT_ID', 'default_value'))


RETRY_PERIOD: int = 600
ENDPOINT: str = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'
HEADERS: dict = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'}

HOMEWORK_VERDICTS: dict = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}


def get_api_answer(timestamp: int) -> dict:
    """Отправить GET-запрос к API сервиса Практикум.Домашка.
    Вернуть ответ API в виде словаря. Вызывать исключения если: эндпойнт
    недоступен, ошибка подключения, ошибка запроса.
    Параметры: timestamp: временная метка.
    """
    logger.debug('************Начало запроса к API')
    try:
        response = requests.get(ENDPOINT, headers=HEADERS,
                                params={'from_date': timestamp})
        if response.status_code != HTTPStatus.OK:
            logger.error(f'Сбой в работе программы: Эндпойнт {ENDPOINT} '
                         f'недоступен. Код ответа API: {response.status_code}')
            raise requests.HTTPError()
        return response.json()
    except requests.ConnectionError:
        raise requests.ConnectionError('Connection error')
    except requests.RequestException as request_error:
        logger.error(f'Request error {request_error}')
    assert False


def send_message(bot: telegram.Bot, message: str) -> None:
    """Отправить сообщение в Telegram чат."""
    logger.debug('Начало отправки сообщения в Telegram')
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logger.debug('Сообщение отправлено в Telegram')
    except TelegramError as error:
        logger.error(f'Сбой при отправке сообщения в Telegram: {error}')


def check_response(response: dict) -> list:
    """Проверить ответ API на соответствие документации сервиса."""
    logger.debug('Начало проверки ответа API.')
    if not isinstance(response, dict):
        raise TypeError('Ответ API не является словарем.')
    if 'homeworks' not in response:
        raise KeyError('Ответ API не содержит ключ homeworks.')
    if not isinstance(response['homeworks'], list):
        raise TypeError('Ответ API под ключом "homeworks" не список.')
    logger.debug('Ответ API корректный.')
    return response['homeworks']


def parse_status(homework: dict) -> str:
    """Извлечь из словаря последней домашки статус ее проверки.
    Возвратить сообщение с назавнием домашки и статусом проверки.
    Вызвать исключение, если в словаре нет ключа homework_name или
    некорректный статус.
    """
    if 'homework_name' not in homework:
        raise KeyError('Ответ не содержит ключ "homework_name"')
    homework_name = homework.get('homework_name')
    logger.debug(f'Последняя домашка: {homework_name}')
    homework_status = homework.get('status')
    logger.debug(f'Статус последней домашки: {homework_status}')
    if homework_status not in HOMEWORK_VERDICTS:
        raise KeyError('Неожиданный статус домашней работы,'
                       'обнаруженный в ответе API')
    verdict = HOMEWORK_VERDICTS.get(homework_status)
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def main():
    """Запустить Telegram-бота.
    Проверить наличие обязательных переменных окружения, отправить
    GET-запрос к API сервиса Практикум.Домашка, проверить ответ и
    отправить в Telegram чат статус домашней работы, повторять запрос
    и проверку каждые 10 минут, при наличии обновлений отправить их
    в Telegram чат. В случае сбоев в работе программы - отправить
    сообщение об этом в Telegram чат.
    """
    current_status: dict = {}
    previous_status: dict = {}
    bot: telegram.Bot = telegram.Bot(token=TELEGRAM_TOKEN)
    timestamp = int(time.time())
    logger.debug('Бот запущен, начало проверки токенов.')
    if not all([PRACTICUM_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        logger.critical('Программа принудительно остановлена из-за '
                        'отсутствия обязательных переменных окружения.')
        sys.exit(0)
    logger.debug('Успешно проверено наличие токенов')
    while True:
        try:
            api_response: dict = get_api_answer(timestamp)
            logger.debug('Получен ответ API.')
            homeworks: list = check_response(api_response)
            if not homeworks:
                logger.info('От API получен пустой список. Нет домашних '
                            'заданий на проверке')
                message: str = 'Нет домашних заданий на проверке'
                current_status['status'] = 'Нет домашних заданий на проверке'
            else:
                logger.debug('В ответе API cписок домашек не пустой.')
                message: str = parse_status(homeworks[0])
                logger.debug('Извлечено сообщение о статусе последней домашки')
                current_status['status'] = message
            if current_status != previous_status:
                send_message(bot, message)
                logger.debug(f'В Telegram отправлено: {message}**************')
                previous_status = current_status.copy()
            else:
                logger.info('В Telegram сообщение не отправлено.')
                logger.debug('Статус последней домашки не изменился '
                             'после предыдущей проверки*************')
                raise SendingError
        except SendingError as error:
            logging.error(error)
        except Exception as error:
            message: str = f'Сбой в работе программы {error}'
            logger.error(message)
            current_status['status'] = message
            if current_status != previous_status:
                send_message(bot, message)
                logger.debug(f'В Telegram отправлено: {message}.')
                previous_status = current_status.copy()
        finally:
            time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    logging.basicConfig(
        filename='main.log',
        filemode='a',
        encoding='utf-8',
    )
    main()
