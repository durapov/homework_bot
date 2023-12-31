import http
import logging
import os
import sys
import telegram
import telegram.ext
import time

import requests

from http import HTTPStatus
from logging import StreamHandler
from telegram import TelegramError

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    filename='main.log',
    filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8',
)
logger = logging.getLogger(__name__)
handler = StreamHandler(stream=sys.stdout)
logger.addHandler(handler)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
handler.setFormatter(formatter)


PRACTICUM_TOKEN = os.getenv('PRACTICUM_TOKEN')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')


RETRY_PERIOD = 20
ENDPOINT = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'
HEADERS = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'}

HOMEWORK_VERDICTS = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}

current_status = {}
previous_status = {}


class SendingError(Exception):
    """Exception."""

    pass


def check_tokens():
    """Проверить доступность переменных окружения.
    Проверить токены Телеграма и Практикум.Домашки, а также ID чата
    в Телеграме (PRACTICUM_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID.).
    """
    missing_tokens = 0
    if not PRACTICUM_TOKEN:
        logger.critical('Отсутствует обязательная переменная окружения '
                        'PRACTICUM_TOKEN')
        missing_tokens += 1
    if not TELEGRAM_TOKEN:
        logger.critical('Отсутствует обязательная переменная окружения '
                        'TELEGRAM_TOKEN.')
        missing_tokens += 1
    if not TELEGRAM_CHAT_ID:
        logger.critical('Отсутствует обязательная переменная окружения '
                        'TELEGRAM_CHAT_ID.')
        missing_tokens += 1
    if missing_tokens != 0:
        logger.critical('Программа принудительно остановлена.')
        sys.exit()
    # проверка доступности Telegram чата
    # bot = telegram.Bot(token=TELEGRAM_TOKEN)
    # try:
    #     bot.send_chat_action(TELEGRAM_CHAT_ID, 'typing')
    # except TelegramError as error:
    #     logger.critical(f'Чат Telegram недоступен: {error}. Программа '
    #                     f'принудительно остановлена.')
    #     sys.exit()


def get_api_answer(timestamp):
    """Отправить GET-запрос к API сервиса Практикум.Домашка.
    Вернуть ответ API в виде словаря. Вызывать исключения если: эндпойнт
    недоступен, ошибка подключения, ошибка запроса.
    Параметры: timestamp: временная метка.
    """
    try:
        response = requests.get(ENDPOINT, headers=HEADERS,
                                params={'from_date': timestamp})
        if response.status_code != HTTPStatus.OK:
            logger.error(f'Сбой в работе программы: Эндпойнт {ENDPOINT} '
                         f'недоступен. Код ответа API: {response.status_code}')
            raise http.exceptions.HTTPError()
        return response.json()
    except requests.ConnectionError:
        raise requests.ConnectionError('Connection error')
    except requests.RequestException as request_error:
        logger.error(f'Request error {request_error}')


def send_message(bot, message):
    """Отправить сообщение в Telegram чат."""
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logger.debug('Сообщение отправлено в Telegram')
    except TelegramError as error:
        logger.error(f'Сбой при отправке сообщения в Telegram: {error}')


def check_response(response):
    """Проверить ответ API на соответствие документации сервиса."""
    if not isinstance(response, dict):
        raise TypeError('Ответ API не является словарем')
    if 'homeworks' not in response:
        raise KeyError('Ответ API не содержит ключ homeworks')
    if not isinstance(response['homeworks'], list):
        raise TypeError('Ответ API под ключом "homeworks" не является списком')
    return response['homeworks']


def check_homeworks(bot, homeworks):
    """Проверить полученный от API список домашек.
    Вернуть Flse, если список домашек пустой, и True, если список
    домашек не пустой.
    """
    global current_status, previous_status
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    if not homeworks:
        logger.info('От API получен пустой список. Нет домашних '
                    'заданий на проверке')
        message = 'Нет домашних заданий на проверке'
        current_status['status'] = 'Нет домашних заданий на проверке'
        if current_status != previous_status:
            send_message(bot, message)
            logger.debug(f'В Telegram отправлено: {message}.')
            previous_status = current_status.copy()
        else:
            logger.error('В Telegram сообщение не отправлено.')
            logger.debug('Статус последней домашки не изменился после '
                         'прошлой проверки****************************')
        return False
    else:
        logger.debug('Список домашек не пустой.')
        return True


def parse_status(homework):
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
    return (f'Изменился статус проверки работы "{homework_name}". {verdict}')


def main():
    """Запустить Telegram-бота.
    Проверить наличие обязательных переменных окружения, отправить
    GET-запрос к API сервиса Практикум.Домашка, проверить ответ и
    отправить в Telegram чат статус домашней работы, повторять запрос
    и проверку каждые 10 минут, при наличии обновлений отправить их
    в Telegram чат. В случае сбоев в работе программы - отправить
    сообщение об этом в Telegram чат.
    """
    global current_status, previous_status
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    timestamp = 1111111111
    logger.debug('***************Бот запущен')
    check_tokens()
    logger.debug('Токены проверены')
    while True:
        try:
            api_response = get_api_answer(timestamp)
            logger.debug('Получен ответ API.')
            homeworks = check_response(api_response)
            if check_homeworks(bot, homeworks):
                message = parse_status(homeworks[0])
                logger.debug('Извлечено сообщение о статусе последней домашки')
                current_status['status'] = message
                if current_status != previous_status:
                    send_message(bot, message)
                    logger.debug(f'В Telegram отправлено: {message}.')
                    previous_status = current_status.copy()
                else:
                    logger.info('В Telegram сообщение не отправлено.')
                    logger.debug('Статус последней домашки не изменился '
                                 'после предыдущей проверки*************')
                    raise SendingError
        except SendingError as error:
            logging.error(error)
        except Exception as error:
            message = f'Сбой в работе программы {error}'
            logger.error(message)
            current_status['status'] = message
            if current_status != previous_status:
                send_message(bot, message)
                logger.debug(f'В Telegram отправлено: {message}.')
                previous_status = current_status.copy()
        time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    main()
