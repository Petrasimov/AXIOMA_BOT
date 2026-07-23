#!/usr/bin/env python3
"""
run_tests.py — Запуск всех тестов AXIOMA_BOT

Использование:

    python3 run_tests.py              все тесты
    python3 run_tests.py -v           подробный вывод
    python3 run_tests.py filters      только модули с "filters" в имени
    python3 run_tests.py payment -v   тесты платежей подробно

Ни база данных, ни сеть, ни реальный бот не нужны — всё внешнее
подменяется заглушками. Никаких дополнительных библиотек ставить
не требуется, используется стандартный unittest.

Код возврата 0 если все тесты прошли, 1 если есть падения —
это позволяет использовать скрипт в CI или git hook.
"""

import sys
import unittest


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    verbose = '-v' in sys.argv or '--verbose' in sys.argv

    loader = unittest.TestLoader()

    if args:
        pattern = f'test*{args[0]}*.py'
        print(f'Запуск тестов по шаблону: {pattern}\n')
    else:
        pattern = 'test_*.py'

    suite = loader.discover(
        start_dir='tests',
        pattern=pattern,
        top_level_dir='.',      # чтобы выполнился tests/__init__.py
    )

    if suite.countTestCases() == 0:
        print('Тесты не найдены.')
        return 1

    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)

    print()
    print('─' * 60)
    print(f'Всего:    {result.testsRun}')
    print(f'Упало:    {len(result.failures)}')
    print(f'Ошибок:   {len(result.errors)}')
    print(f'Пропущено:{len(result.skipped)}')
    print('─' * 60)

    if result.wasSuccessful():
        print('✅ Все тесты прошли')
        return 0

    print('❌ Есть проблемы')
    return 1


if __name__ == '__main__':
    sys.exit(main())