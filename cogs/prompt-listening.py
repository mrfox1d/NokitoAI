import disnake
from disnake.ext.commands import Cog, slash_command, command
from data.interaction import Database
from openai import OpenAI
import time
import os
import json
from datetime import timedelta
from dotenv import load_dotenv
from typing import Optional, Tuple, List

load_dotenv()

# Используем TOKEN из .env (убедись, что там именно ключ OpenRouter)
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENAI_TOKEN"),
)

db = Database()

SYSTEM_PROMPT = """
Ты — ИИ-бот на Discord-сервере блогера Nokitohack (Сергей Тарасик).
Стиль: короткие ответы (1–3 предложения), юмор, сарказм, геймерский сленг.
Не душни, не пиши длинно, будь "своим". Если не знаешь — шути.
"""

# ID админов
ADMIN_IDS = [123456789]  # Замени на реальные ID

# Цвета для эмбедов
COLORS = {
    "primary": 0x5865F2,      # Синий
    "success": 0x57F287,      # Зеленый
    "warning": 0xF26522,      # Оранжевый
    "error": 0xED4245,        # Красный
    "neutral": 0x2b2d31,      # Темный серый
}


class UserMemory:
    """Система памяти для пользователя"""
    
    def __init__(self, max_messages: int = 10):
        """
        max_messages: максимальное количество сообщений в истории
        """
        self.max_messages = max_messages
        self.memory = {}  # {user_id: [{"role": "user/assistant", "content": "...", "timestamp": 12345}]}
    
    def add_message(self, user_id: int, role: str, content: str):
        """Добавить сообщение в память пользователя"""
        if user_id not in self.memory:
            self.memory[user_id] = []
        
        self.memory[user_id].append({
            "role": role,
            "content": content,
            "timestamp": int(time.time())
        })
        
        # Удаляем старые сообщения, если превышен лимит
        if len(self.memory[user_id]) > self.max_messages:
            self.memory[user_id].pop(0)
    
    def get_memory(self, user_id: int) -> List[dict]:
        """Получить всю память пользователя"""
        return self.memory.get(user_id, [])
    
    def clear_memory(self, user_id: int):
        """Очистить память пользователя"""
        if user_id in self.memory:
            self.memory[user_id] = []
    
    def get_memory_summary(self, user_id: int) -> str:
        """Получить краткое резюме памяти для промпта"""
        messages = self.get_memory(user_id)
        if not messages:
            return ""
        
        summary = "Предыдущие сообщения пользователя:\n"
        for msg in messages[-5:]:  # Последние 5 сообщений в резюме
            if msg['role'] == 'user':
                summary += f"- {msg['content']}\n"
        
        return summary


# Инициализируем память
user_memory = UserMemory(max_messages=15)


class PromptListening(Cog):
    def __init__(self, bot):
        self.bot = bot

    def is_admin(self, user_id: int) -> bool:
        """Проверяет, админ ли пользователь"""
        return user_id in ADMIN_IDS

    def create_progress_bar(self, spent: int, total: int, size: int = 15) -> str:
        """Создает визуальную полоску лимитов"""
        if total == 0:
            return "✨ Бесконечно"
        
        filled = int((spent / total) * size)
        percentage = int((spent / total) * 100)
        
        bar = "🔴" * filled + "⚪" * (size - filled)
        return f"{bar} **{percentage}%**"

    def get_embed_color(self, spent: int, total: int) -> int:
        """Возвращает цвет эмбеда в зависимости от процента использования"""
        if total == 0:
            return COLORS["success"]
        
        percentage = (spent / total) * 100
        
        if percentage >= 100:
            return COLORS["error"]
        elif percentage >= 75:
            return COLORS["warning"]
        else:
            return COLORS["success"]

    async def get_ai_response(
        self,
        user_id: int,
        content: str,
        use_memory: bool = True
    ) -> Tuple[str, Optional[str]]:
        """
        Получить ответ от ИИ с опциональной памятью
        
        Args:
            user_id: ID пользователя Discord
            content: Текст вопроса
            use_memory: Использовать ли историю сообщений пользователя
            
        Returns:
            Tuple (ответ, примечание_о_лимите)
        """
        await db.reset_limit(user_id)
        user_data = await db.get_user(user_id=user_id)
        
        total = user_data[1]
        spent = user_data[2]

        if spent >= total:
            return "❌ Лимит достигнут. Жди обновления или попроси у админа больше запросов.", None

        try:
            # Подготавливаем сообщения с памятью
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
            
            # Добавляем память пользователя в контекст
            if use_memory:
                memory_items = user_memory.get_memory(user_id)
                for msg in memory_items:
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"]
                    })
            
            # Добавляем текущее сообщение
            messages.append({"role": "user", "content": content})
            
            # Отправляем запрос к ИИ
            response = client.chat.completions.create(
                model="deepseek/deepseek-v3.2",
                messages=messages,
                max_tokens=1000
            )
            
            reply_text = response.choices[0].message.content
            
            # Сохраняем в память
            user_memory.add_message(user_id, "user", content)
            user_memory.add_message(user_id, "assistant", reply_text)
            
            # Обновляем счетчик
            new_spent = spent + 1
            await db.update_user(user_id=user_id, spent_today=new_spent)
            
            # Подготавливаем футер с информацией о лимитах
            footer = f"Использовано: {new_spent}/{total} | Осталось: {total - new_spent}"
            
            return reply_text, footer
            
        except Exception as e:
            return f"❌ Ошибка API: {str(e)}", None

    @Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        
        # Проверка канала и упоминания
        if message.channel.id == 1484810154842263632:
            if self.bot.user in message.mentions and not message.mention_everyone:
                content = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
                if not content:
                    return

                async with message.channel.typing():
                    reply, footer = await self.get_ai_response(message.author.id, content, use_memory=True)
                    
                    # Создаем эмбед
                    color = self.get_embed_color(0, 1)  # Получаем цвет
                    user_data = await db.get_user(user_id=message.author.id)
                    spent = user_data[2]
                    total = user_data[1]
                    color = self.get_embed_color(spent, total)
                    
                    embed = disnake.Embed(
                        description=reply,
                        color=color
                    )
                    embed.set_author(
                        name="NokitoAI",
                        icon_url=self.bot.user.avatar.url
                    )
                    if footer:
                        embed.set_footer(text=footer)
                    
                    await message.reply(embed=embed)

    # ========== ОСНОВНЫЕ КОМАНДЫ ==========

    @slash_command(name="ask", description="Задать вопрос нейросети")
    async def ask(
        self,
        inter: disnake.ApplicationCommandInteraction,
        prompt: str,
        use_memory: bool = True
    ):
        """Слэш-команда для запросов с опциональной памятью"""
        await inter.response.defer()
        
        reply, footer = await self.get_ai_response(inter.author.id, prompt, use_memory=use_memory)
        
        # Получаем информацию о лимитах для цвета
        user_data = await db.get_user(user_id=inter.author.id)
        spent = user_data[2]
        total = user_data[1]
        color = self.get_embed_color(spent, total)
        
        embed = disnake.Embed(
            description=reply,
            color=color
        )
        embed.set_author(
            name="NokitoAI",
            icon_url=self.bot.user.avatar.url
        )
        if footer:
            embed.set_footer(text=footer)
        
        await inter.edit_original_response(embed=embed)

    @slash_command(name="info", description="Твоя статистика")
    async def info(self, inter: disnake.ApplicationCommandInteraction):
        """Показывает статистику пользователя с красивым визуалом"""
        await inter.response.defer(ephemeral=True)
        await db.reset_limit(inter.author.id)
        
        user = await db.get_user(user_id=inter.author.id)
        spent = user[2]
        total = user[1]
        reset_time = int(user[3])
        
        seconds_left = max(0, reset_time - int(time.time()))
        time_formatted = str(timedelta(seconds=seconds_left))
        progress = self.create_progress_bar(spent, total)
        available = max(0, total - spent)
        color = self.get_embed_color(spent, total)

        embed = disnake.Embed(
            title="💾 Личный кабинет NokitoAI",
            color=color
        )
        embed.add_field(
            name="📊 Статус лимитов",
            value=f"{progress}\n`{spent}` / `{total}` запросов",
            inline=False
        )
        embed.add_field(
            name="✅ Доступно",
            value=f"**{available}** запросов",
            inline=True
        )
        embed.add_field(
            name="⏰ Обновление",
            value=f"`{time_formatted}`",
            inline=True
        )
        
        embed.set_thumbnail(url=inter.author.display_avatar.url)
        embed.set_footer(text="NokitoAI • /ask для вопросов • /memory для управления памятью")
        
        await inter.edit_original_response(embed=embed)

    @slash_command(name="memory", description="Управление памятью бота")
    async def memory_management(
        self,
        inter: disnake.ApplicationCommandInteraction,
        action: str = "view"
    ):
        """
        Управление памятью бота
        
        action: view (посмотреть), clear (очистить)
        """
        await inter.response.defer(ephemeral=True)
        
        user_id = inter.author.id
        memory = user_memory.get_memory(user_id)
        
        if action.lower() == "clear":
            user_memory.clear_memory(user_id)
            embed = disnake.Embed(
                title="🧠 Память очищена",
                description="Я забыл всё о наших предыдущих разговорах",
                color=COLORS["warning"]
            )
            embed.set_footer(text="В следующих вопросах я буду отвечать без контекста")
            
        elif action.lower() == "view":
            if not memory:
                embed = disnake.Embed(
                    title="🧠 Твоя память пуста",
                    description="У меня нет сохраненных сообщений о тебе",
                    color=COLORS["neutral"]
                )
            else:
                embed = disnake.Embed(
                    title="🧠 Моя память о тебе",
                    color=COLORS["primary"]
                )
                
                # Показываем последние 5 сообщений
                for i, msg in enumerate(memory[-5:], 1):
                    role = "❓ Ты" if msg["role"] == "user" else "🤖 Я"
                    content = msg["content"][:100] + "..." if len(msg["content"]) > 100 else msg["content"]
                    
                    embed.add_field(
                        name=f"{role}",
                        value=f"*{content}*",
                        inline=False
                    )
                
                embed.set_footer(text=f"Всего в памяти: {len(memory)} сообщений")
        else:
            embed = disnake.Embed(
                title="❌ Неизвестная команда",
                description="Используй: `view` или `clear`",
                color=COLORS["error"]
            )
        
        await inter.edit_original_response(embed=embed)

    @slash_command(name="help", description="Справка по командам")
    async def help_command(self, inter: disnake.ApplicationCommandInteraction):
        """Справка по всем командам"""
        await inter.response.defer(ephemeral=True)

        embed = disnake.Embed(
            title="📚 Справка по командам",
            color=COLORS["primary"],
            description="Полный список команд NokitoAI"
        )
        
        embed.add_field(
            name="/ask `<вопрос>` [use_memory: true/false]",
            value="❓ Задай вопрос нейросети\n`use_memory: true` - с учетом истории (по умолчанию)",
            inline=False
        )
        embed.add_field(
            name="/info",
            value="📊 Посмотри свою статистику и лимиты",
            inline=False
        )
        embed.add_field(
            name="/memory [view/clear]",
            value="🧠 Управляй памятью бота\n• `view` - посмотреть память\n• `clear` - очистить память",
            inline=False
        )
        embed.add_field(
            name="💬 Просто упомяни бота",
            value="Можешь просто написать в канале @NokitoAI вопрос и ответ придет в эмбеде",
            inline=False
        )
        
        embed.add_field(
            name="🔧 Система памяти",
            value="Бот автоматически запоминает твои сообщения и учитывает их в ответах. Это помогает ему лучше тебя понимать!",
            inline=False
        )
        
        embed.set_footer(text="NokitoAI • Nokitohack Server")
        await inter.edit_original_response(embed=embed)

    # ========== АДМИН КОМАНДЫ ==========

    @slash_command(name="admin_limit", description="[АДМИН] Изменить лимит пользователю")
    async def admin_limit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User,
        new_limit: int
    ):
        """Изменяет дневной лимит пользователю"""
        
        if not self.is_admin(inter.author.id):
            embed = disnake.Embed(
                title="❌ Доступ запрещен",
                description="Только администраторы могут использовать эту команду",
                color=COLORS["error"]
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        if new_limit < 0:
            embed = disnake.Embed(
                title="❌ Ошибка",
                description="Лимит не может быть отрицательным",
                color=COLORS["error"]
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        await inter.response.defer()

        try:
            await db.update_user(user_id=user.id, daily_limit=new_limit)
            
            embed = disnake.Embed(
                title="✅ Лимит изменен",
                description=f"Пользователю {user.mention} установлен лимит: **{new_limit}** запросов/день",
                color=COLORS["success"]
            )
            embed.set_footer(text=f"Изменено администратором: {inter.author.name}")
            
            await inter.edit_original_response(embed=embed)
        except Exception as e:
            embed = disnake.Embed(
                title="❌ Ошибка базы данных",
                description=f"Не удалось изменить лимит: {e}",
                color=COLORS["error"]
            )
            await inter.edit_original_response(embed=embed)

    @slash_command(name="admin_add_limit", description="[АДМИН] Добавить запросы пользователю")
    async def admin_add_limit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User,
        amount: int
    ):
        """Добавляет запросы к текущему лимиту"""
        
        if not self.is_admin(inter.author.id):
            embed = disnake.Embed(
                title="❌ Доступ запрещен",
                description="Только администраторы могут использовать эту команду",
                color=COLORS["error"]
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        await inter.response.defer()

        try:
            user_data = await db.get_user(user_id=user.id)
            current_limit = user_data[1]
            new_limit = current_limit + amount

            await db.update_user(user_id=user.id, daily_limit=new_limit)
            
            action = "добавлено" if amount > 0 else "удалено"
            color = COLORS["success"] if amount > 0 else COLORS["warning"]
            
            embed = disnake.Embed(
                title="✅ Лимит обновлен",
                description=f"Пользователю {user.mention} {action} **{abs(amount)}** запросов\n"
                           f"Новый лимит: **{new_limit}** запросов/день",
                color=color
            )
            embed.set_footer(text=f"Изменено администратором: {inter.author.name}")
            
            await inter.edit_original_response(embed=embed)
        except Exception as e:
            embed = disnake.Embed(
                title="❌ Ошибка",
                description=f"Не удалось обновить лимит: {e}",
                color=COLORS["error"]
            )
            await inter.edit_original_response(embed=embed)

    @slash_command(name="admin_reset_spent", description="[АДМИН] Обнулить потраченные запросы")
    async def admin_reset_spent(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User
    ):
        """Обнуляет количество потраченных запросов"""
        
        if not self.is_admin(inter.author.id):
            embed = disnake.Embed(
                title="❌ Доступ запрещен",
                description="Только администраторы могут использовать эту команду",
                color=COLORS["error"]
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        await inter.response.defer()

        try:
            await db.update_user(user_id=user.id, spent_today=0)
            
            embed = disnake.Embed(
                title="✅ Счетчик обнулен",
                description=f"Потраченные запросы {user.mention} обнулены",
                color=COLORS["success"]
            )
            embed.set_footer(text=f"Изменено администратором: {inter.author.name}")
            
            await inter.edit_original_response(embed=embed)
        except Exception as e:
            embed = disnake.Embed(
                title="❌ Ошибка",
                description=f"Не удалось обнулить счетчик: {e}",
                color=COLORS["error"]
            )
            await inter.edit_original_response(embed=embed)

    @slash_command(name="admin_check_user", description="[АДМИН] Проверить статистику пользователя")
    async def admin_check_user(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User
    ):
        """Проверяет детальную информацию о пользователе"""
        
        if not self.is_admin(inter.author.id):
            embed = disnake.Embed(
                title="❌ Доступ запрещен",
                description="Только администраторы могут использовать эту команду",
                color=COLORS["error"]
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        await inter.response.defer(ephemeral=True)

        try:
            user_data = await db.get_user(user_id=user.id)
            spent = user_data[2]
            total = user_data[1]
            reset_time = int(user_data[3])
            
            seconds_left = max(0, reset_time - int(time.time()))
            time_formatted = str(timedelta(seconds=seconds_left))
            progress = self.create_progress_bar(spent, total)
            
            # Проверяем память пользователя
            user_memory_count = len(user_memory.get_memory(user.id))
            
            embed = disnake.Embed(
                title=f"👤 Статистика {user.name}",
                color=COLORS["primary"]
            )
            embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
            embed.add_field(name="Статус", value="Активный ✅", inline=True)
            embed.add_field(
                name="📊 Лимиты",
                value=f"{progress}\n`{spent}` / `{total}`",
                inline=False
            )
            embed.add_field(
                name="⏰ Обновление через",
                value=f"`{time_formatted}`",
                inline=True
            )
            embed.add_field(
                name="🧠 Сообщений в памяти",
                value=f"`{user_memory_count}`",
                inline=True
            )
            embed.add_field(
                name="📅 Дата присоединения",
                value=f"{user.created_at.strftime('%d.%m.%Y')}",
                inline=True
            )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text=f"Проверено администратором: {inter.author.name}")
            
            await inter.edit_original_response(embed=embed)
        except Exception as e:
            embed = disnake.Embed(
                title="❌ Ошибка",
                description=f"Не удалось загрузить данные: {e}",
                color=COLORS["error"]
            )
            await inter.edit_original_response(embed=embed)

def setup(bot):
    bot.add_cog(PromptListening(bot))